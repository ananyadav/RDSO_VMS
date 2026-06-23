# Phase 3 — Stable Live Streaming Baseline

This document records the **last known stable live-view configuration** (validated May–June 2026 on Windows with 16 Hikvision cameras). No architecture redesign — baseline only.

## Stable configuration summary

| View | RTSP channel | Codec path | FFmpeg |
|------|--------------|------------|--------|
| **Grid (Live View tiles)** | **102** substream | H.264 **copy** (`-c:v copy`) | One process per camera |
| **Fullscreen modal** | **101** main (default) | H.264/H.265 **copy** (no transcode) | Separate process per camera (`{id}__fullscreen`) |
| **Fullscreen fallback** | **102** substream | H.264 copy | Auto if main/101 fails |

**Not used in baseline:** MP4 output, CPU transcoding, H.265 re-encode on grid, WebRTC for grid (legacy WebRTC code remains in repo but is not the Live View path).

### Recommended `.env` (live)

```env
HLS_SEGMENT_SECONDS=1
HLS_LIST_SIZE=3
HLS_LIVE_STREAM=sub
HLS_FULLSCREEN_STREAM=main
HLS_MAX_CONCURRENT_STARTS=8
HLS_READY_TIMEOUT=4
HLS_RTSP_TIMEOUT_US=5000000
CAMERA_PREVIEW_CHANNEL=103
```

### Copy-mode segment duration vs `HLS_SEGMENT_SECONDS`

`HLS_SEGMENT_SECONDS` is a **target** for FFmpeg’s HLS muxer. With **`-c:v copy`** (no transcode), segments can only be cut on **keyframes (I-frames)**. If the camera GOP / iframe interval is ~3–4 seconds, measured `#EXTINF` in `live.m3u8` will be **~3.3–4s** even when `HLS_SEGMENT_SECONDS=1`.

This is expected for copy-mode live HLS:

| Factor | Effect |
|--------|--------|
| Camera keyframe interval (GOP) | Minimum segment length in copy mode |
| `independent_segments` HLS flag | Segments start on keyframes only |
| No re-encode | Cannot force 1s segments without transcoding |

**Diagnostics:** compare `hlsSegmentSecondsConfigured` (env) vs `hlsSegmentDurationSec` (parsed playlist). A large gap indicates camera GOP, not a misconfigured FFmpeg flag.

To shorten segments without transcode, lower the camera’s **I-frame interval** in its web UI (e.g. 1s GOP) — that is a camera setting, not an NVR code change.

Optional: `HLS_FULLSCREEN_STREAM=preview` uses channel **103** (H.264) instead of 101, still with sub/102 fallback.

---

## End-to-end stream flow

```
Camera (RTSP)
    │
    ▼
FFmpeg (one per grid camera, copy-only, no audio)
    │  RTSP TCP, sub/102 for grid
    │  main/101 for fullscreen stream id
    ▼
HLS segments on disk
    │  {NVR_LIVE_DIR or %TEMP%/nvr_live}/{streamId}/live.m3u8 + seg*.ts
    ▼
Backend aiohttp
    │  POST /api/live/{streamId}/start   → subscribe / start FFmpeg
    │  GET  /api/live/{streamId}/live.m3u8
    │  GET  /api/live/{streamId}/seg*.ts
    ▼
Browser (hls.js)
    │  useLiveHLS → liveStreamRegistry (ref-counted start/stop)
    ▼
<video> tile (grid) or fullscreen modal
```

### Stream IDs

| UI | `streamId` | Playlist URL |
|----|------------|--------------|
| Grid tile | `{cameraObjectId}` | `/api/live/{cameraId}/live.m3u8` |
| Fullscreen | `{cameraObjectId}__fullscreen` | `/api/live/{cameraId}__fullscreen/live.m3u8` |

Grid and fullscreen are **independent** FFmpeg processes so opening fullscreen does not disturb the substream grid.

---

## Frontend flow

1. **Live View** loads cameras → `batchStartLiveStreams(cameraIds)` (POST `/api/live/batch-start`).
2. Each **CameraCard** calls `acquireLiveStream(cameraId)` → POST `/api/live/{cameraId}/start`.
3. **useLiveHLS** attaches hls.js to `/api/live/{cameraId}/live.m3u8` (substream 102).
4. **FullscreenCameraModal** uses `acquireLiveStream(cameraId, fullscreen=true)` and playlist `/api/live/{id}__fullscreen/live.m3u8`.
5. On unmount, `releaseLiveStream` decrements refs; FFmpeg stops when refs reach 0.

Player tuning (low latency): `lowLatencyMode: true`, `liveSyncDurationCount: 1`, `maxBufferLength: 4`, live-edge seek + 3s maintenance in `useLiveHLS.ts` / `hlsLiveEdge.ts`.

---

## Backend flow (`video_live_hls.py`)

1. `subscribe(stream_id)` increments ref-count; starts FFmpeg if first viewer.
2. `_pick_grid_urls` → always `sub_rtsp_url` (channel 102).
3. `_pick_fullscreen_urls` → `main_rtsp_url` (101) unless `HLS_FULLSCREEN_STREAM=preview|sub`.
4. FFmpeg command: **copy only** `-c:v copy`, HLS muxer, target 1s segments (actual length = camera GOP), list size 3.
5. **Windows:** `omit_endlist+independent_segments` (no `delete_segments` — avoids file-lock errors).
6. **Linux:** adds `delete_segments` for disk control.
7. On FFmpeg exit:
   - Fullscreen **main/101** or **preview/103** → restart on **sub/102**.
   - Otherwise → restart same profile after 1.5s.

RTSP URLs are built in `rtsp_utils.py` and stored on the camera document (`main_rtsp_url`, `sub_rtsp_url`, `preview_rtsp_url`).

---

## What was stable (June 2026)

- 16-camera grid with batch start and ref-counted subscriptions.
- Substream 102, H.264 copy, ~1–2s latency after buffer tuning.
- Windows fix: no segment delete during live playback.
- hls.js retry/recovery for network and media errors.
- IntersectionObserver lazy-load for tiles (optional; batch mode uses `eagerLive`).

## Known limits

- **453 Not Enough Bandwidth** on cameras when too many RTSP clients — reduce concurrent starts or use substream only.
- **Main/101 in browser** may be H.265; copy is still used (no transcode). If playback fails, backend falls back to sub/102 automatically.
- Legacy **WebRTC** path in `video_streaming.py` is unchanged; Live View uses **HLS only**.

## VLC / external player

1. Start stream: `POST http://SERVER:10000/api/live/{cameraId}/start`
2. Open: `http://SERVER:10000/api/live/{cameraId}/live.m3u8`

Fullscreen (main): use `{cameraId}__fullscreen` in both URLs.

## Files (baseline)

| Layer | File |
|-------|------|
| Backend HLS | `backend/app/services/video_live_hls.py` |
| RTSP URLs | `backend/app/services/rtsp_utils.py` |
| Routes | `backend/app/routes/live.py` |
| Frontend hook | `frontend/src/hooks/useLiveHLS.ts` |
| Live edge helpers | `frontend/src/lib/hlsLiveEdge.ts` |
| Ref-count registry | `frontend/src/lib/liveStreamRegistry.ts` |
| Grid UI | `frontend/src/pages/LiveView.tsx`, `CameraCard.tsx` |
| Fullscreen UI | `frontend/src/components/FullscreenCameraModal.tsx` |
| Live diagnostics | `frontend/src/pages/LiveDiagnostics.tsx`, `GET /api/live/diagnostics` |
| QA checklist | `docs/LIVE_VIEW_TEST_CHECKLIST.md` |
