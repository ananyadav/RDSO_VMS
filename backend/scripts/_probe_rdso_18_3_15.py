"""Read-only RDSO 18.3.15 probe against one safe camera."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import camera_collection
from app.services.edge_capability import detect_edge_storage_capability
from app.services.onvif_stream_uri import resolve_onvif_profile_s_streams
from app.services.rtsp_utils import mask_rtsp_url


async def main() -> None:
    cam = await camera_collection.find_one({"protocol": {"$regex": "^ONVIF$", "$options": "i"}})
    if not cam:
        cam = await camera_collection.find_one({"ip_address": "192.168.41.90"})
    if not cam:
        cam = await camera_collection.find_one({})
    if not cam:
        print("NO_CAMERA")
        return
    print("camera", cam.get("name"), cam.get("ip_address"), cam.get("protocol"))
    try:
        s = await resolve_onvif_profile_s_streams(cam)
        print("profile_s_supported", s.get("supported"))
        print("profile_s_message", s.get("message"))
        main = s.get("main") or {}
        masked = main.get("rtsp_uri_masked")
        if not masked and main.get("rtsp_uri"):
            masked = mask_rtsp_url(main["rtsp_uri"])
        print("main_masked", masked)
        print("sub_ok", (s.get("sub") or {}).get("ok"))
    except Exception as exc:
        print("profile_s_error", exc)
    try:
        g = await detect_edge_storage_capability(cam)
        print("profile_g_supported", g.get("supported"), g.get("protocol"))
        print("profile_g_message", (g.get("message") or "")[:200])
    except Exception as exc:
        print("profile_g_error", exc)


if __name__ == "__main__":
    asyncio.run(main())
