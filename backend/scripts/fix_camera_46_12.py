"""One-off: fix MBF HBS HEATING AREA (192.168.46.12) RTSP paths."""
import asyncio
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.core.database import camera_collection
from app.services.go2rtc_service import ensure_go2rtc_streams, write_config_file

IP = "192.168.46.12"
USER = "admin"
PW = "Rashmi@432"
PORT = 554


async def main() -> None:
    u = urllib.parse.quote(USER, safe="")
    p = urllib.parse.quote(PW, safe="")
    main_url = f"rtsp://{u}:{p}@{IP}:{PORT}/11"
    sub_url = f"rtsp://{u}:{p}@{IP}:{PORT}/ch01/sub/av_stream"

    cam = await camera_collection.find_one({"ip_address": IP})
    if not cam:
        print("camera not found")
        return

    fields = {
        "protocol": "CUSTOM",
        "model": "Sparsh",
        "main_rtsp_url": main_url,
        "sub_rtsp_url": sub_url,
        "preview_rtsp_url": sub_url,
        "rtsp_url": sub_url,
        "rtsp_url_source": "manual",
    }
    await camera_collection.update_one({"_id": cam["_id"]}, {"$set": fields})
    print("updated", cam.get("name"), cam["_id"])

    built = await write_config_file()
    print("sub:", built["masked"].get("ip_192_168_46_12_sub"))
    print("main:", built["masked"].get("ip_192_168_46_12_main"))

    result = await ensure_go2rtc_streams()
    print("go2rtc sync ok:", result.get("ok"))


if __name__ == "__main__":
    asyncio.run(main())
