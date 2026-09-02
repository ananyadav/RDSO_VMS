import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from bson import ObjectId
from app.core.database import camera_collection
from app.services.onvif_media import (
    _configuration_to_set_xml,
    _find_first,
    _get_encoder_config,
    _get_profiles,
    _media_soap,
    _set_text,
)
from app.services.onvif_ptz import _TRT, _TT


async def main() -> None:
    cam = await camera_collection.find_one({"_id": ObjectId("6a6ad1d5ab17995b58f6cb92")})
    profiles, _ = await _get_profiles(cam)
    enc = profiles[1]["encoder_token"]
    token, fields, elem, err = await _get_encoder_config(cam, enc)
    print("token", token, "fields", fields, "err", err)
    rate = _find_first(elem, "RateControl")
    _set_text(rate, "FrameRateLimit", "12")
    set_config = _configuration_to_set_xml(token, elem)
    body = (
        f'<trt:SetVideoEncoderConfiguration xmlns:trt="{_TRT}" xmlns:tt="{_TT}">'
        f"{set_config}"
        f"<trt:ForcePersistence>true</trt:ForcePersistence>"
        f"</trt:SetVideoEncoderConfiguration>"
    )
    print("BODY\n", body)
    status, text, url = await _media_soap(cam, f"{_TRT}/SetVideoEncoderConfiguration", body)
    print("status", status, "url", url)
    m = re.search(r"<[^:]*:?Text[^>]*>([^<]+)", text)
    print("fault", m.group(1) if m else text[:1200])


if __name__ == "__main__":
    asyncio.run(main())
