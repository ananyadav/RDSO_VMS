# Phase 3 — Live View Test Checklist

Manual QA sequence for stable live streaming (grid + fullscreen + diagnostics).  
Use after Phase 3 Steps 1–8 are deployed.

**Related docs:** [STREAMING_BASELINE.md](STREAMING_BASELINE.md)  
**Diagnostics UI:** Sidebar → **Live Diag** (`/live-diagnostics`)  
**Diagnostics API:** `GET /api/live/diagnostics`

---

## Prerequisites

| Item | Expected |
|------|----------|
| Backend | `http://localhost:10000` (or production host) |
| Frontend | `http://localhost:3000` |
| `.env` live settings | `HLS_SEGMENT_SECONDS=1`, `HLS_LIST_SIZE=3`, `HLS_KEEP_WARM_SECONDS=30`, `LIVE_BATCH_SIZE=4`, `LIVE_BATCH_DELAY_MS=750` |
| Cameras | Substream **102** = **H.264** (grid); at least 4 online for early steps |
| Browser | Hard refresh (`Ctrl+Shift+R`) before testing |

### Fresh start (Step 1)

```powershell
# Backend
cd backend
python -m app.main

# Frontend (separate terminal)
cd frontend
npm run dev
```

- [ ] Backend logs show `[HLS]` startup with `segment=1s`, `flags=delete_segments+omit_endlist+independent_segments`
- [ ] No stale FFmpeg from a prior session (Task Manager / `GET /api/live/diagnostics` → `ffmpegProcessCount: 0` before opening Live View)

---

## Test sequence

### Steps 1–4 — Four-camera grid

| # | Action | Pass criteria | ☐ |
|---|--------|---------------|---|
| **1** | Start backend and frontend fresh | Both services up; diagnostics empty or 0 FFmpeg | ☐ |
| **2** | Open **Live View** with **4 cameras** | Use **2×2** layout; 4 online tiles visible | ☐ |
| **3** | Wait for batch + tile connect (~10–20s) | All **4 tiles show live video** (not blank, not stuck on “Connecting…”) | ☐ |
| **4** | Open **Live Diag** or `GET /api/live/diagnostics` | `activeStreamCount` ≥ 4, `ffmpegProcessCount` = **4**; every row `profile: grid`; **no** `__fullscreen` stream ids | ☐ |

**Step 4 detail**

```json
{
  "activeStreamCount": 4,
  "ffmpegProcessCount": 4,
  "streams": [ /* 4 rows, profile "grid", unique streamId per camera */ ]
}
```

- [ ] Each stream has unique `ffmpegPid`
- [ ] `playlistReady: yes` for all 4 (or becomes yes within `HLS_READY_TIMEOUT`)
- [ ] Tile badges show **H.264 supported** (or explicit error — not silent blank)

---

### Steps 5–9 — Fullscreen without disturbing grid

| # | Action | Pass criteria | ☐ |
|---|--------|---------------|---|
| **5** | Double-click (or fullscreen button) **one** camera | Fullscreen modal opens | ☐ |
| **6** | Observe fullscreen player | Status: **Connecting** → **Playing** (or **Fallback to substream** if main/101 fails) | ☐ |
| **7** | Check diagnostics | **4 grid** streams still present **+ 1 fullscreen** (`streamId` ends with `__fullscreen`) → `ffmpegProcessCount` = **5** | ☐ |
| **8** | Close fullscreen (ESC or X) | Modal unmounts; **grid tile for that camera still playing** | ☐ |
| **9** | Watch same grid tile 10–15s | **No frozen/old frames**; video stays at live edge (no full reconnect flash on tile) | ☐ |

**Step 7 note:** After close, fullscreen stream may show `status: warming` for up to `HLS_KEEP_WARM_SECONDS` (30s) before FFmpeg exits — that is expected. Grid streams must remain **4** with `profile: grid` throughout.

**Step 9 note:** Grid must **not** restart FFmpeg for the opened camera (check diagnostics: same grid `ffmpegPid` before and after fullscreen, or stable playback without tile “Connecting…” again).

---

### Steps 10–13 — Scale and stability

| # | Action | Pass criteria | ☐ |
|---|--------|---------------|---|
| **10** | **8 cameras** online in grid (e.g. 3×3 or 4×2 layout) | All 8 tiles play; diagnostics: **8** grid FFmpeg processes | ☐ |
| **11** | **16 cameras** (4×4 layout, ≤24 triggers eager batch) | All 16 tiles play or show explicit error (codec/offline); no mass blank grid | ☐ |
| **12** | Review diagnostics table | `ffmpegProcessCount` = number of **unique** `streamId` with alive PID; **no duplicate PIDs** for same `streamId` | ☐ |
| **13** | Monitor host CPU/RAM 5+ min during 16-cam grid | CPU not pegged at 100% sustained; memory not climbing unbounded; backend responsive | ☐ |

**Step 12 — duplicate check**

- [ ] One `ffmpegPid` per `streamId`
- [ ] `refCount` ≥ 1 for active viewers; no orphan double-starts from batch + tile acquire (frontend `syncBatchGridRefs` aligned)

**Step 13 — where to look**

- Windows: Task Manager → Python (backend) + browser
- Diagnostics auto-refresh: startup ms stable; `lastError` null on healthy streams

---

### Step 14 — Recording & playback regression

| # | Action | Pass criteria | ☐ |
|---|--------|---------------|---|
| **14a** | **Storage** page | Pilot/recording status loads; no new errors in backend logs | ☐ |
| **14b** | **Playback** — pick camera + date with known footage | Sessions list, timeline, and video play (not “Recording file not found” for valid files) | ☐ |
| **14c** | Optional: start/stop recording toggle on one camera | Recording health unchanged; live grid still works | ☐ |

Live View testing must **not** break recording FFmpeg processes or playback search.

---

## Quick API checks

```bash
# Summary
curl -s http://localhost:10000/api/live/diagnostics | jq .

# Count grid vs fullscreen
curl -s http://localhost:10000/api/live/diagnostics | jq '[.streams[] | .profile] | group_by(.) | map({profile: .[0], count: length})'
```

---

## Failure triage

| Symptom | Likely cause | Check |
|---------|--------------|--------|
| Blank tile, no message | H.265 on sub 102 in Chrome | Tile badge / codec error overlay; set camera substream to H.264 |
| `ffmpegProcessCount` > camera count | Duplicate subscribe or warm + active | Live Diag `refCount`, stream ids; restart backend |
| Fullscreen kills grid | Wrong stream id or shared release | Grid `{id}` vs fullscreen `{id}__fullscreen` in diagnostics |
| 16-cam partial blank | Batch/network spike | Logs `[HLS] Batch`; increase `LIVE_BATCH_DELAY_MS`; camera 453 bandwidth |
| Playback broken after live test | Unrelated — verify MongoDB sessions + files on disk | Playback search logs `[PLAYBACK]` |

---

## Sign-off

| Tester | Date | 4-cam | Fullscreen | 16-cam | Rec/Playback | Notes |
|--------|------|-------|------------|--------|--------------|-------|
| | | ☐ | ☐ | ☐ | ☐ | |
