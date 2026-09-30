import asyncio
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
from app.core.database import camera_collection

async def main():
    good = await camera_collection.find_one({"ip_address": "192.168.46.150"}, {"username":1,"password":1,"protocol":1})
    bad156 = await camera_collection.find_one({"ip_address": "192.168.46.156"}, {"username":1,"password":1,"protocol":1,"sub_rtsp_url":1,"main_rtsp_url":1})
    bad170 = await camera_collection.find_one({"ip_address": "192.168.46.170"}, {"username":1,"password":1,"protocol":1,"sub_rtsp_url":1,"main_rtsp_url":1})
    print("good.150 user", good.get("username"), "pass_len", len(str(good.get("password") or "")), "proto", good.get("protocol"))
    print("bad.156 user", bad156.get("username"), "pass_len", len(str(bad156.get("password") or "")), "same_pass_as_150", (bad156.get("password")==good.get("password")), "sub", (bad156.get("sub_rtsp_url") or "")[:60])
    print("bad.170 user", bad170.get("username"), "pass_len", len(str(bad170.get("password") or "")), "same_pass_as_150", (bad170.get("password")==good.get("password")), "sub", (bad170.get("sub_rtsp_url") or "")[:70])

asyncio.run(main())
