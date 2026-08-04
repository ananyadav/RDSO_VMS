import asyncio, sys
from urllib.parse import quote
from dotenv import load_dotenv
load_dotenv("../.env")
sys.path.insert(0, ".")
from app.core.database import camera_collection
from app.services.ffmpeg_util import ffmpeg_bin
from app.services.rtsp_utils import mask_rtsp_url

IP = "192.168.44.206"
PATHS = [
    "/Streaming/Channels/101",
    "/Streaming/Channels/102",
    "/Streaming/Channels/1",
    "/Streaming/Channels/2",
    "/h264/ch1/main/av_stream",
    "/h264/ch1/sub/av_stream",
    "/cam/realmonitor?channel=1&subtype=0",
    "/cam/realmonitor?channel=1&subtype=1",
    "/media/video1",
    "/media/video2",
    "/live/ch00_0",
    "/live/ch00_1",
    "/live0.264",
    "/live1.264",
    "/ch0_0.h264",
    "/ch0_1.h264",
    "/user=admin&password={pwd}&channel=1&stream=0.sdp?",
    "/user=admin&password={pwd}&channel=1&stream=1.sdp?",
    "/user=admin_password={pwd}_channel=1_stream=0.sdp?real_stream",
    "/user=admin_password={pwd}_channel=1_stream=1.sdp?real_stream",
    "/Streaming/Unicast/channels/101",
    "/Streaming/Unicast/channels/102",
    "/onvif1",
    "/onvif2",
    "/stream1",
    "/stream2",
    "/videoMain",
    "/videoSub",
    "/av0_0",
    "/av0_1",
]

async def probe(url, t=5):
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
        "-stimeout", "4000000", "-i", url, "-frames:v", "1", "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=t)
    except asyncio.TimeoutError:
        try:
            proc.kill(); await proc.wait()
        except Exception:
            pass
        return False, "timeout"
    return proc.returncode == 0, (err or b"").decode("utf-8", "replace").strip().replace("\n"," ")[:140]

async def main():
    cam = await camera_collection.find_one({"ip_address": IP})
    user = quote(cam.get("username") or "admin", safe="")
    pwd_raw = cam.get("password") or ""
    pwd = quote(pwd_raw, safe="")
    print("cam", IP, "pwd_set", bool(pwd_raw))
    # also try onvif via go2rtc? skip
    for path in PATHS:
        p = path.format(pwd=pwd)
        if p.startswith("/user="):
            url = f"rtsp://{IP}:554{p}"
        else:
            url = f"rtsp://{user}:{pwd}@{IP}:554{p}"
        ok, detail = await probe(url)
        mark = "OK" if ok else ".."
        print(f"{mark} {mask_rtsp_url(url)} | {detail}")
        if ok:
            print("FOUND", mask_rtsp_url(url))
            return
    print("NONE WORKED")

asyncio.run(main())
