# go2rtc Phase 1 — Realtime Live Pilot

HLS V1 (`video_live_hls.py`) remains the production fallback. This folder adds a **go2rtc** relay for low-latency WebRTC/MSE trials.

## Setup

1. Download [go2rtc release](https://github.com/AlexxIT/go2rtc/releases) for your OS.
2. Place binary in `go2rtc/bin/go2rtc.exe` (Windows) or `go2rtc/bin/go2rtc` (Linux).
3. Ensure **Cam18** exists in MongoDB with valid RTSP credentials.
4. Set in `.env` (optional):
   ```env
   GO2RTC_ENABLED=true
   GO2RTC_PILOT_CAMERA=Cam18
   ```
5. Restart backend — it writes `go2rtc/runtime/go2rtc.yaml` and starts go2rtc on **:1984**.

## Streams (one RTSP each inside go2rtc)

| Name | Channel | Use (future) |
|------|---------|----------------|
| `Cam18_sub` | 102 | Grid |
| `Cam18_main` | 101 | Fullscreen |

## Test

- UI: **Sidebar → RT Live** → `/live-realtime-test`
- API: `GET /api/go2rtc/status`, `POST /api/go2rtc/start`
- Direct go2rtc UI: `http://127.0.0.1:1984` (when running)

## Architecture notes

- Recording / playback unchanged (main 101, copy-only).
- go2rtc holds **one RTSP session per stream name** — avoids duplicate pulls for WebRTC + MSE viewers.
- Hikvision browser live (port 7681 / F1vPlayer) is the reference; we use go2rtc as the Frigate-style relay.
