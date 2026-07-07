import asyncio
import json
import logging
import os
import platform
import socket
import subprocess
import urllib.parse
import psutil
from datetime import datetime
from fractions import Fraction
from typing import List, Optional


# ---------------------------------------------------------------------------
# Detect the primary LAN IP of this server so we can filter ICE candidates.
# Docker bridges (172.x), loopback, and other non-LAN IPs cause ICE failure
# because the browser client cannot reach them.
# ---------------------------------------------------------------------------
def _get_server_lan_ip() -> str:
    """Return the IP of the interface used to reach the default gateway."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""

SERVER_LAN_IP: str = _get_server_lan_ip()
logging.info(f"[ICE] Server LAN IP detected as: {SERVER_LAN_IP}")


def filter_sdp_ice_candidates(sdp: str, allowed_ip: str) -> str:
    """
    Remove ICE host candidates for known-unreachable IPs (Docker bridges, loopback).

    Strategy: BLOCK-LIST bad prefixes instead of allow-listing a single IP.
    This is safer — if the preferred LAN IP hasn't been gathered yet when the
    ICE timeout fires, we still leave other valid candidates rather than
    sending an answer with ZERO candidates (which causes instant ICE failure).
    """
    # IPs that are never reachable from a LAN browser
    BAD_PREFIXES = (
        "127.",       # loopback
        "172.17.",    # docker0 bridge
        "172.18.",    # docker bridge
        "172.19.",    # docker bridge
        "172.20.",    # docker bridge
        "::1",        # IPv6 loopback
        "fe80::",     # link-local IPv6
    )

    sep = "\r\n" if "\r\n" in sdp else "\n"
    lines = sdp.split(sep)
    filtered = []
    removed = 0

    for line in lines:
        if line.startswith("a=candidate:"):
            parts = line.split()
            if len(parts) >= 5:
                candidate_ip = parts[4]
                if any(candidate_ip.startswith(bad) for bad in BAD_PREFIXES):
                    removed += 1
                    continue   # drop unreachable candidate
        filtered.append(line)

    # Safety: if filtering removed ALL candidates, fall back to original SDP
    remaining = sum(1 for l in filtered if l.startswith("a=candidate:"))
    if remaining == 0 and removed > 0:
        logging.warning("[ICE] All candidates were filtered out — returning unfiltered SDP as fallback")
        return sdp

    if removed:
        logging.info(f"[ICE] Removed {removed} unreachable ICE candidate(s). {remaining} remain.")
    return sep.join(filtered)

from aiohttp import web, WSMsgType
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaStreamTrack
import av

from app.core.database import camera_collection
from app.core.auth_context import get_effective_user
from app.services.camera_access import is_admin, user_can_access_camera
from app.services.camera_identity import get_camera_by_ref
from bson import ObjectId

# --- Global State ---
pcs = set()
CAMERA_TRACKS = {}  # track_id -> FFmpegTrack
TRACK_LOCKS = {}    # track_id -> asyncio.Lock

# --- GPU device selection (round-robin) ---
_GPU_ROTATION_LOCK = asyncio.Lock()
_GPU_ROTATION_INDEX = 0


def _parse_gpu_rotation() -> List[str]:
    """
    Returns list of GPU indices to use for CUDA hwaccel.

    Supported env vars:
    - GPU_DEVICE_ROTATION="0,1,2" (documented)
    - VIDEO_HWACCEL_DEVICES="0,1,2" (alias)
    - VIDEO_HWACCEL_DEVICE="0" (single device fallback)
    """
    rotation = (os.getenv("GPU_DEVICE_ROTATION") or os.getenv("VIDEO_HWACCEL_DEVICES") or "").strip()
    if rotation:
        devices = [d.strip() for d in rotation.split(",") if d.strip() != ""]
        devices = [d for d in devices if d.isdigit()]
        if devices:
            return devices
    # fallback to single device
    single = (os.getenv("VIDEO_HWACCEL_DEVICE", "0") or "0").strip()
    return [single if single.isdigit() else "0"]


async def _choose_cuda_device() -> str:
    """Round-robin device selection so streams spread across multiple GPUs."""
    global _GPU_ROTATION_INDEX
    devices = _parse_gpu_rotation()
    async with _GPU_ROTATION_LOCK:
        dev = devices[_GPU_ROTATION_INDEX % len(devices)]
        _GPU_ROTATION_INDEX += 1
        return dev


def get_video_decode_mode() -> dict:
    """
    Returns current video decode mode (GPU vs CPU) for status/confirmation.
    Same logic used when starting FFmpeg for each stream.
    """
    use_gpu = (
        platform.system() == "Linux"
        and (os.getenv("VIDEO_HWACCEL") or "").strip().lower() == "cuda"
    )
    return {
        "mode": "gpu" if use_gpu else "cpu",
        "description": "NVIDIA CUDA (NVDEC)" if use_gpu else "CPU (software)",
    }


# --- Performance Monitoring ---
class PerformanceMonitor:
    """Monitor system performance and log statistics."""
    def __init__(self, interval=60):
        self.interval = interval
        self.monitoring = False
        self.monitor_task = None
        self.stats = {
            "cpu_percent": 0,
            "memory_percent": 0,
            "memory_mb": 0,
            "active_cameras": 0,
            "total_frames_processed": 0,
            "ffmpeg_processes": 0,
        }

    async def start_monitoring(self):
        if self.monitoring:
            return
        self.monitoring = True
        self.monitor_task = asyncio.create_task(self._monitoring_loop())

    async def stop_monitoring(self):
        if not self.monitoring:
            return
        self.monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()

    async def _monitoring_loop(self):
        while self.monitoring:
            try:
                await self._collect_stats()
                self._log_stats()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Performance monitoring error: {e}")
                await asyncio.sleep(self.interval)

    async def _collect_stats(self):
        try:
            self.stats["cpu_percent"] = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            self.stats["memory_percent"] = memory.percent
            self.stats["memory_mb"] = memory.used / 1024 / 1024

            self.stats["active_cameras"] = len(CAMERA_TRACKS)
            self.stats["ffmpeg_processes"] = len(
                [p for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == "ffmpeg"]
            )

            total_frames = 0
            for track in CAMERA_TRACKS.values():
                total_frames += track.frame_counter
            self.stats["total_frames_processed"] = total_frames
        except Exception as e:
            logging.warning(f"Failed to collect performance stats: {e}")

    def _log_stats(self):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = (
            f"[{timestamp}] Performance: "
            f"CPU: {self.stats['cpu_percent']:.1f}%, "
            f"Memory: {self.stats['memory_mb']:.0f}MB ({self.stats['memory_percent']:.1f}%), "
            f"Active Cameras: {self.stats['active_cameras']}, "
            f"FFmpeg Processes: {self.stats['ffmpeg_processes']}, "
            f"Total Frames: {self.stats['total_frames_processed']}"
        )
        logging.info(log_msg)

performance_monitor = PerformanceMonitor(interval=30)

# --- CAMERA SOURCES ---
# You can still optionally hardcode sources here (not required when using DB cameras).
CAMERA_SOURCES = {}

# Cache probe results so each camera is only probed once (avoids repeated blocking calls)
_PROBE_CACHE: dict = {}


# --- Codec Detection ---
def probe_stream_info(url: str):
    """Probe the video stream for codec and resolution.
    Results are cached so each unique URL is only probed once.
    """
    if url in _PROBE_CACHE:
        return _PROBE_CACHE[url]

    # Sanitize URL for logging (hide password)
    if "@" in url and "://" in url:
        log_url = url.split("://")[0] + "://" + url.split("@")[-1]
    else:
        log_url = url

    try:
        cmd = [
            "ffprobe",
            "-rtsp_transport", "tcp",        # <-- added
            "-v", "error",
            "-show_streams",
            "-print_format", "json",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)

        if result.returncode != 0:
            error_msg = (result.stderr or result.stdout or "").strip() or f"exit code {result.returncode}"
            error_lower = error_msg.lower()

            if any(k in error_lower for k in ["401", "unauthorized", "authentication", "login", "password", "access denied"]):
                logging.warning(f"ffprobe authentication failed for {log_url}: Check camera credentials")
            elif any(k in error_lower for k in ["connection refused", "network is unreachable", "no route to host", "connection timed out", "could not connect"]):
                logging.warning(f"ffprobe connection failed for {log_url}: Camera may be offline or unreachable")
            elif "404" in error_msg or "not found" in error_lower or "does not exist" in error_lower:
                logging.warning(f"ffprobe stream not found for {log_url}: Check RTSP path/channel number")
            elif "timeout" in error_lower or "timed out" in error_lower:
                logging.warning(f"ffprobe timeout for {log_url}: Camera may be slow to respond or unreachable")
            else:
                logging.warning(f"ffprobe failed for {log_url}")

            logging.warning(f"  Error: {error_msg[:400]}")
            return None, None, None

        data = json.loads(result.stdout or "{}")
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                result_tuple = (stream.get("codec_name"), stream.get("width"), stream.get("height"))
                _PROBE_CACHE[url] = result_tuple  # cache success
                return result_tuple

        logging.warning(f"ffprobe succeeded for {log_url} but no video stream found")
        return None, None, None

    except subprocess.TimeoutExpired:
        logging.warning(f"ffprobe timeout for {log_url} (camera may be slow to respond or unreachable)")
    except Exception as e:
        logging.error(f"Error probing codec for {log_url}: {e}")

    return None, None, None


# --- WebRTC Video Streaming ---
class FFmpegTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, camera_id: str, is_fullscreen: bool = False):
        super().__init__()
        self.camera_id = camera_id
        self.is_fullscreen = is_fullscreen

        self.proc = None
        self.queue = asyncio.Queue(maxsize=200)

        self.codec = None
        self.width = None
        self.height = None
        self.frame_size = None

        self.pts_counter = 0
        self._task = None
        self._stderr_task = None

        self.client_count = 0
        self.running = False
        self.frame_counter = 0

    async def _read_stderr(self):
        """Log stderr lines from ffmpeg (bytes -> str), filtering non-critical warnings."""
        # Non-critical FFmpeg messages to filter out
        ignored_patterns = [
            "deprecated pixel format",
            "Could not find ref with POC",
            "Error constructing the frame RPS",
            "Skipping invalid undecodable NALU",
            "CSeq",
            "More than 1000 frames duplicated",
            "frame=",  # Progress output
            "fps=",
            "bitrate=",
            "size=",
            "time=",
            "Metadata:",
            "encoder",
            "libav",
            "configuration:",
            "built with",
            "Input #",
            "Output #",
            "Stream #",
            "Stream mapping:",
            "Press [q] to stop",
        ]
        
        try:
            while True:
                if not self.proc or not self.proc.stderr:
                    break
                line = await self.proc.stderr.readline()
                if not line:
                    break
                msg = line.decode("utf-8", errors="ignore").strip()
                if msg:
                    # Check if this is a critical error (not in ignored patterns)
                    is_critical = not any(pattern.lower() in msg.lower() for pattern in ignored_patterns)
                    
                    # Always log critical errors, but only log warnings at debug level
                    if is_critical:
                        # Check for actual errors (not just info messages)
                        if any(keyword in msg for keyword in ["ERROR", "Failed", "error", "failed", "Error number"]):
                            logging.error(f"[{self.camera_id} ffmpeg] {msg}")
                        else:
                            logging.debug(f"[{self.camera_id} ffmpeg] {msg}")
        except asyncio.CancelledError:
            return
        except Exception as e:
            logging.debug(f"[{self.camera_id}] Stderr reader error (expected on shutdown): {e}")

    async def _start_ffmpeg(self):
        source = CAMERA_SOURCES.get(self.camera_id)

        if not source:
            # Pull from DB
            try:
                cam_oid = ObjectId(self.camera_id)
                camera_doc = await camera_collection.find_one({"_id": cam_oid})
                if not camera_doc:
                    logging.error(f"Camera '{self.camera_id}' not found in DB.")
                    return

                cam_name = camera_doc.get("name", "Unknown Camera")

                # Prefer pre-built RTSP URLs stored in the document
                main_rtsp = camera_doc.get("main_rtsp_url")
                sub_rtsp = camera_doc.get("sub_rtsp_url") or main_rtsp

                if main_rtsp:
                    rtsp_url = main_rtsp if self.is_fullscreen else (sub_rtsp or main_rtsp)
                    # Mask credentials for logging
                    import re as _re
                    log_url = _re.sub(r'(rtsp://[^:]+:)[^@]+(@)', r'\1***\2', rtsp_url)
                    logging.info(f"Using stored RTSP URL for camera '{self.camera_id}': {log_url}")
                    source = {"type": "rtsp", "url": rtsp_url, "name": cam_name}
                else:
                    # Fall back to building URL from individual fields
                    channel = "101" if self.is_fullscreen else "102"
                    username = (camera_doc.get("username") or "admin").strip()
                    password = camera_doc.get("password")

                    if password is None or str(password).strip() == "":
                        logging.error(
                            f"Camera '{self.camera_id}' has no password and no pre-built RTSP URL. "
                            f"Update it from Camera Management."
                        )
                        return

                    ip_address = (camera_doc.get("ip_address") or "").strip()
                    port = camera_doc.get("port", 554)
                    model = (camera_doc.get("model") or "").lower()

                    if not ip_address:
                        logging.error(f"Camera '{self.camera_id}' missing IP address.")
                        return

                    password_encoded = urllib.parse.quote(str(password).strip(), safe="")
                    username_encoded = urllib.parse.quote(username, safe="")

                    if "dahua" in model:
                        channel_num = channel.lstrip("0") or "1"
                        rtsp_path = f"/cam/realmonitor?channel={channel_num}&subtype=0"
                    elif "axis" in model:
                        rtsp_path = "/axis-media/media.amp"
                    else:
                        rtsp_path = f"/Streaming/Channels/{channel}"

                    rtsp_url = f"rtsp://{username_encoded}:{password_encoded}@{ip_address}:{port}{rtsp_path}"
                    log_url = f"rtsp://{username}:***@{ip_address}:{port}{rtsp_path}"
                    logging.info(f"Using built RTSP URL for camera '{self.camera_id}': {log_url}")
                    source = {"type": "rtsp", "url": rtsp_url, "name": cam_name}

            except Exception as e:
                logging.error(f"Invalid camera ID '{self.camera_id}': {e}")
                return

        # Build ffmpeg command
        if source["type"] == "local":
            operating_system = platform.system()
            if operating_system == "Windows":
                device_options = ["-f", "dshow", "-i", "video=Integrated Camera"]
            elif operating_system == "Linux":
                device_options = ["-f", "v4l2", "-i", "/dev/video0"]
            else:
                logging.error(f"Unsupported OS for local camera: {operating_system}")
                return

            self.codec = av.codec.CodecContext.create("h264", "r")
            ffmpeg_cmd = [
                "ffmpeg", *device_options,
                "-an", "-c:v", "copy",
                "-f", "h264", "pipe:1",
            ]

        elif source["type"] == "rtsp":
            # Probe codec/resolution once for frame size.
            # IMPORTANT: run in a thread so the blocking subprocess.run()
            # doesn't freeze the asyncio event loop (which would prevent
            # other cameras' WebSocket handshakes from completing).
            logging.info(f"Probing codec for camera '{self.camera_id}'...")
            codec_name, self.width, self.height = await asyncio.to_thread(
                probe_stream_info, source["url"]
            )

            # If probe fails, try common alternative paths
            if not codec_name and "@" in source["url"]:
                logging.warning(f"Primary RTSP path failed for camera '{self.camera_id}', trying alternative paths...")
                try:
                    auth_part, rest = source["url"].split("@", 1)
                    host_part = rest.split("/", 1)[0]
                    base_url = f"{auth_part}@{host_part}"
                    alternatives = [
                        "/cam/realmonitor?channel=1&subtype=0",
                        "/axis-media/media.amp",
                        "/live",
                        "/video",
                        "/stream1",
                    ]
                    for alt_path in alternatives:
                        alt_url = f"{base_url}{alt_path}"
                        codec_name, self.width, self.height = await asyncio.to_thread(
                            probe_stream_info, alt_url
                        )
                        if codec_name:
                            source["url"] = alt_url
                            logging.info(f"Connected using alternative path: {alt_path}")
                            break
                except Exception as e:
                    logging.warning(f"Error trying alternative RTSP paths: {e}")

            if not codec_name or not self.width or not self.height:
                log_url = source["url"]
                if "@" in log_url and "://" in log_url:
                    log_url = log_url.split("://")[0] + "://" + log_url.split("@")[-1]
                logging.error(f"Failed to probe codec for camera '{self.camera_id}'")
                logging.error(f"  RTSP URL: {log_url}")
                return

            self.frame_size = int(self.width) * int(self.height) * 3 // 2
            logging.info(f"Detected codec '{codec_name}' for camera '{self.camera_id}' at {self.width}x{self.height}")

            # GPU (NVIDIA NVDEC) or CPU decode. On Ubuntu, set VIDEO_HWACCEL=cuda to use GPU.
            use_gpu = (
                platform.system() == "Linux"
                and os.getenv("VIDEO_HWACCEL", "").strip().lower() == "cuda"
            )
            codec_lower = (codec_name or "").lower()
            gpu_decoder = None
            if use_gpu:
                if codec_lower in ("h264", "avc1"):
                    gpu_decoder = "h264_cuvid"
                elif codec_lower in ("hevc", "h265"):
                    gpu_decoder = "hevc_cuvid"
                else:
                    use_gpu = False
                    logging.info(f"GPU decode not available for codec '{codec_name}', using CPU")

            if use_gpu and gpu_decoder:
                # NVIDIA NVDEC: decode on GPU, then hwdownload to CPU for pipe (yuv420p).
                hwaccel_device = await _choose_cuda_device()
                vf_parts = []
                if self.is_fullscreen:
                    # Force a fixed output size to keep frame_size correct for rawvideo reads.
                    vf_parts.append("scale_cuda=1280:720")
                # Download GPU frame to system memory and convert to yuv420p for python pipe.
                # NOTE: hwdownload cannot reliably output yuv420p directly on some builds/streams.
                # Download as NV12, then let the output stage convert to yuv420p via -pix_fmt.
                vf_parts.append("hwdownload,format=nv12")
                if self.is_fullscreen:
                    # Apply fps after download (works reliably on CPU frames).
                    vf_parts.append("fps=15")
                vf = ",".join(vf_parts) if vf_parts else None

                ffmpeg_cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-hwaccel", "cuda",
                    "-hwaccel_device", hwaccel_device,
                    "-hwaccel_output_format", "cuda",
                    "-fflags", "+genpts+discardcorrupt",
                    "-rtsp_transport", "tcp",
                    # IMPORTANT: decoder selection must come BEFORE -i to affect the input.
                    "-c:v", gpu_decoder,
                    "-i", source["url"],
                    "-an",
                ]
                if vf:
                    ffmpeg_cmd += ["-vf", vf]
                ffmpeg_cmd += [
                    "-pix_fmt", "yuv420p",
                    "-f", "rawvideo",
                    "pipe:1",
                ]
                logging.info(f"Using GPU decode ({gpu_decoder}) for camera '{self.camera_id}'")
                if self.is_fullscreen and vf:
                    self.width = 1280
                    self.height = 720
                    self.frame_size = int(self.width) * int(self.height) * 3 // 2
            else:
                # CPU decode. Optional: for fullscreen reduce load with scale+fps (lanczos).
                logging.info(f"Using CPU decode for camera '{self.camera_id}'")
                vf = None
                if self.is_fullscreen:
                    # Fixed size keeps rawvideo frame sizing correct.
                    vf = "scale=1280:720:flags=lanczos,fps=15"

                ffmpeg_cmd = [
                    "ffmpeg",
                    "-fflags", "+genpts+discardcorrupt",
                    "-rtsp_transport", "tcp",
                    "-i", source["url"],
                    "-an",
                ]
                if vf:
                    ffmpeg_cmd += ["-vf", vf]
                ffmpeg_cmd += [
                    "-f", "rawvideo",
                    "-pix_fmt", "yuv420p",
                    "pipe:1",
                ]
                if self.is_fullscreen and vf:
                    self.width = 1280
                    self.height = 720
                    self.frame_size = int(self.width) * int(self.height) * 3 // 2

        else:
            logging.error(f"Unsupported camera type: {source['type']}")
            return

        logging.info(f"Starting FFmpeg for camera '{self.camera_id}' with command: {' '.join(ffmpeg_cmd)}")

        # Python 3.12: binary pipes - explicitly ensure no text mode
        # stdout and stderr must be bytes, not text
        self.proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Explicitly disable text mode to ensure bytes
            text=False,
            bufsize=0,  # Unbuffered for real-time streaming
        )

        self._task = asyncio.create_task(self._read_frames())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def _enqueue_frame(self, frame):
        queue_utilization = self.queue.qsize() / self.queue.maxsize
        should_drop = False
        if queue_utilization >= 0.9:
            should_drop = (self.frame_counter % 3) != 0
        elif queue_utilization >= 0.8:
            should_drop = (self.frame_counter % 2) != 0
        if should_drop:
            return
        try:
            await asyncio.wait_for(self.queue.put(frame), timeout=0.1)
        except asyncio.TimeoutError:
            logging.warning(f"[{self.camera_id}] Queue full, dropping frame")

    async def _read_frames(self):
        if self.codec:
            # Encoded (local camera) path
            while True:
                try:
                    if not self.proc or not self.proc.stdout:
                        break
                    data = await self.proc.stdout.read(4096)
                    if not data:
                        break
                    packets = self.codec.parse(data)
                    for packet in packets:
                        for frame in self.codec.decode(packet):
                            self.frame_counter += 1
                            await self._enqueue_frame(frame)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logging.error(f"FFmpeg read error for {self.camera_id}: {e}")
                    break
        else:
            # Raw video path
            if not self.frame_size:
                logging.error(f"[{self.camera_id}] Frame size not set for raw video")
                return

            while True:
                try:
                    if not self.proc or not self.proc.stdout:
                        break

                    frame_data = await self.proc.stdout.readexactly(self.frame_size)

                    # Ensure frame_data is bytes (CPU-only mode, no GPU/cuvid)
                    # PyAV's from_bytes requires a bytes object, not string, not memoryview
                    # Convert any type to bytes explicitly
                    if isinstance(frame_data, str):
                        logging.warning(f"[{self.camera_id}] Frame data is string, converting to bytes")
                        frame_data = frame_data.encode('latin-1')
                    elif isinstance(frame_data, memoryview):
                        frame_data = frame_data.tobytes()
                    elif isinstance(frame_data, bytearray):
                        frame_data = bytes(frame_data)
                    elif not isinstance(frame_data, bytes):
                        logging.error(f"[{self.camera_id}] Frame data is unexpected type: {type(frame_data)}")
                        break

                    # Double-check: ensure we have the right length of bytes
                    if len(frame_data) != self.frame_size:
                        logging.warning(f"[{self.camera_id}] Frame data size mismatch: expected {self.frame_size}, got {len(frame_data)}")
                        break

                    # Convert to bytes if needed
                    if isinstance(frame_data, memoryview):
                        frame_data = frame_data.tobytes()
                    elif isinstance(frame_data, bytearray):
                        frame_data = bytes(frame_data)
                    elif isinstance(frame_data, str):
                        frame_data = frame_data.encode('latin-1')
                    elif not isinstance(frame_data, bytes):
                        logging.error(f"[{self.camera_id}] Frame data is unexpected type: {type(frame_data)}")
                        break
                    
                    frame_bytes = bytes(frame_data)
                    
                    try:
                        width = int(self.width)
                        height = int(self.height)
                        
                        # YUV420P is planar format:
                        # Y plane: width × height bytes (luma)
                        # U plane: (width/2) × (height/2) bytes (chroma)
                        # V plane: (width/2) × (height/2) bytes (chroma)
                        y_size = width * height
                        uv_size = (width // 2) * (height // 2)
                        
                        # Extract plane data
                        y_data = frame_bytes[0:y_size]
                        u_data = frame_bytes[y_size:y_size + uv_size]
                        v_data = frame_bytes[y_size + uv_size:y_size + 2 * uv_size]
                        
                        # Create VideoFrame with YUV420P format and set planes manually
                        frame = av.VideoFrame(width=width, height=height, format='yuv420p')
                        frame.planes[0].update(y_data)
                        frame.planes[1].update(u_data)
                        frame.planes[2].update(v_data)
                        
                    except Exception as e:
                        logging.error(f"[{self.camera_id}] Failed to create VideoFrame from YUV420P: {e}")
                        logging.error(f"  Frame data type: {type(frame_bytes)}")
                        logging.error(f"  Frame data length: {len(frame_bytes)}, expected: {self.frame_size}")
                        logging.error(f"  Width: {self.width}, Height: {self.height}")
                        raise
                    frame.pts = self.pts_counter
                    frame.dts = self.pts_counter
                    frame.time_base = Fraction(1, 30)
                    frame.key_frame = True

                    self.pts_counter += 1
                    self.frame_counter += 1

                    await self._enqueue_frame(frame)

                except asyncio.IncompleteReadError:
                    break
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    error_msg = str(e)
                    logging.error(f"FFmpeg read error for {self.camera_id}: {e}")
                    if 'frame_data' in locals():
                        logging.error(f"  Frame data type: {type(frame_data)}")
                        logging.error(f"  Frame data length: {len(frame_data) if hasattr(frame_data, '__len__') else 'N/A'}")
                    break

    async def recv(self):
        return await self.queue.get()

    async def start_ffmpeg(self):
        if self.running:
            return
        self.running = True
        await self._start_ffmpeg()

    async def stop_ffmpeg(self):
        if not self.running:
            return
        self.running = False

        # Cancel reading tasks first
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logging.debug(f"[{self.camera_id}] Task cancellation error (expected): {e}")
        
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logging.debug(f"[{self.camera_id}] Stderr task cancellation error (expected): {e}")

        # Properly terminate and wait for process
        if self.proc:
            try:
                if self.proc.returncode is None:
                    # Try graceful termination first
                    try:
                        self.proc.terminate()
                        # Wait with timeout to avoid hanging
                        try:
                            await asyncio.wait_for(self.proc.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            # Force kill if terminate didn't work
                            logging.warning(f"[{self.camera_id}] FFmpeg process didn't terminate, forcing kill")
                            try:
                                self.proc.kill()
                                await asyncio.wait_for(self.proc.wait(), timeout=1.0)
                            except Exception as e:
                                logging.debug(f"[{self.camera_id}] Process kill error (expected): {e}")
                    except ProcessLookupError:
                        # Process already terminated
                        pass
                    except Exception as e:
                        logging.debug(f"[{self.camera_id}] Process termination error (expected on shutdown): {e}")
                
                # Close pipes to prevent resource warnings
                try:
                    if self.proc.stdout:
                        self.proc.stdout.close()
                except Exception:
                    pass
                try:
                    if self.proc.stderr:
                        self.proc.stderr.close()
                except Exception:
                    pass
            except Exception as e:
                logging.debug(f"[{self.camera_id}] Cleanup error (expected on shutdown): {e}")

        logging.info(f"Stopped FFmpeg for camera '{self.camera_id}'")


# --- Track Management (safe locking) ---
def _get_lock(track_id: str) -> asyncio.Lock:
    if track_id not in TRACK_LOCKS:
        TRACK_LOCKS[track_id] = asyncio.Lock()
    return TRACK_LOCKS[track_id]


async def get_or_create_track(track_id: str):
    lock = _get_lock(track_id)
    async with lock:
        track = CAMERA_TRACKS.get(track_id)
        if not track:
            is_fullscreen = track_id.endswith("_fullscreen")
            actual_camera_id = track_id.replace("_fullscreen", "") if is_fullscreen else track_id
            track = FFmpegTrack(actual_camera_id, is_fullscreen)
            CAMERA_TRACKS[track_id] = track
        return track


async def subscribe_to_track(track_id: str):
    track = await get_or_create_track(track_id)
    lock = _get_lock(track_id)
    async with lock:
        if track.client_count == 0:
            await track.start_ffmpeg()
        track.client_count += 1
        logging.info(f"Subscribed to camera '{track_id}', client count: {track.client_count}")
    return track


async def unsubscribe_from_track(track_id: str):
    track = CAMERA_TRACKS.get(track_id)
    if not track:
        return
    lock = _get_lock(track_id)
    async with lock:
        track.client_count -= 1
        logging.info(f"Unsubscribed from camera '{track_id}', client count: {track.client_count}")
        if track.client_count <= 0:
            CAMERA_TRACKS.pop(track_id, None)
            TRACK_LOCKS.pop(track_id, None)
            await track.stop_ffmpeg()


async def cleanup_all_tracks():
    """Cleanup all active tracks. Call this on server shutdown to prevent asyncio errors."""
    logging.info("Cleaning up all camera tracks...")
    track_ids = list(CAMERA_TRACKS.keys())
    for track_id in track_ids:
        try:
            track = CAMERA_TRACKS.get(track_id)
            if track:
                await track.stop_ffmpeg()
        except Exception as e:
            logging.debug(f"Error cleaning up track {track_id} (expected on shutdown): {e}")
    CAMERA_TRACKS.clear()
    TRACK_LOCKS.clear()
    logging.info("All camera tracks cleaned up.")


# --- WebSocket Handler (aiohttp - same port as API to avoid proxy/HTTP2 issues) ---
async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    """
    WebRTC video streaming WebSocket. Served on the same port as the API (/ws)
    to avoid HTTP/2, reverse proxy, and multi-port connection issues.
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    user = await get_effective_user(request)
    if user is None:
        await ws.close(code=4401, message=b"Authentication required")
        return ws

    def exception_handler(loop, context):
        message = context.get("message", "")
        exception = context.get("exception")
        exception_type = type(exception).__name__ if exception else "Unknown"
        if any(k in message for k in ["RTCIceTransport is closed", "InvalidStateError", "invalid state", "Connection closed"]) \
           or exception_type in ["InvalidStateError", "ConnectionError", "CancelledError"]:
            logging.debug(f"aiortc exception handled: {exception_type} - {message}")
        else:
            logging.error(f"Unhandled exception in WebRTC handler: {exception_type} - {message}")
            if exception:
                logging.error(f"Exception details: {exception}")

    loop = asyncio.get_event_loop()
    original_handler = loop.get_exception_handler()
    loop.set_exception_handler(exception_handler)

    pc = RTCPeerConnection()
    pcs.add(pc)
    track_id = None
    camera_id_for_log = "Unknown"

    async def wait_for_ice_gathering_complete(timeout_s: float = 2.0):
        if pc.iceGatheringState == "complete":
            return
        done = asyncio.Event()
        @pc.on("icegatheringstatechange")
        def on_ice_gathering_state_change():
            if pc.iceGatheringState == "complete":
                done.set()
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logging.debug("ICE gathering timeout; sending best-effort SDP answer")

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logging.info(f"ICE connection state is {pc.iceConnectionState}")
        if pc.iceConnectionState == "failed":
            await pc.close()

    try:
        msg = await ws.receive()
        if msg.type != WSMsgType.TEXT:
            logging.warning(f"WebSocket expected TEXT, got {msg.type}")
            return ws

        data = json.loads(msg.data)
        logging.info(f"Received WebSocket data: {data}")

        camera_id = data.get("cameraId")
        is_fullscreen = data.get("isFullscreen", False)

        if not camera_id:
            logging.error("No cameraId provided in WebSocket offer.")
            return ws

        camera_id_for_log = camera_id
        cam = await get_camera_by_ref(camera_id)
        if not is_admin(user) and not user_can_access_camera(user, camera_id, cam):
            await ws.send_str(json.dumps({"error": "Camera access denied"}))
            return ws

        track_id = camera_id + "_fullscreen" if is_fullscreen else camera_id
        logging.info(f"Received offer for camera: {camera_id}, fullscreen: {is_fullscreen}, using track: {track_id}")

        video_track = await subscribe_to_track(track_id)

        offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
        await pc.setRemoteDescription(offer)
        pc.addTrack(video_track)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await wait_for_ice_gathering_complete(timeout_s=5.0)

        # Filter out non-LAN ICE candidates (Docker bridges, loopback) so the
        # browser only tries the reachable server IP.
        raw_sdp = pc.localDescription.sdp
        answer_sdp = filter_sdp_ice_candidates(raw_sdp, SERVER_LAN_IP)

        # Log candidate summary for debugging
        raw_cands = [l for l in raw_sdp.splitlines() if l.startswith("a=candidate:")]
        kept_cands = [l for l in answer_sdp.splitlines() if l.startswith("a=candidate:")]
        logging.info(f"[ICE] Camera {camera_id_for_log}: {len(raw_cands)} raw candidates → {len(kept_cands)} after filter")
        for c in kept_cands:
            logging.info(f"[ICE]  ↳ {c}")

        await ws.send_str(json.dumps({"sdp": answer_sdp, "type": "answer"}))

        # Keep connection open until client closes (required for WebRTC data channel)
        async for msg in ws:
            if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR, WSMsgType.CLOSING):
                break

    except Exception as e:
        logging.info(f"WebSocket error for camera: {camera_id_for_log}: {e}")

    finally:
        if pc in pcs:
            pcs.discard(pc)
            await pc.close()
        if track_id:
            await unsubscribe_from_track(track_id)
        loop.set_exception_handler(original_handler)

    return ws
