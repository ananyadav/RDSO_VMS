import asyncio, sys
from dotenv import load_dotenv
load_dotenv("../.env")
sys.path.insert(0, ".")
from app.core.database import camera_collection
from app.services.ffmpeg_util import ffmpeg_bin
from app.services.rtsp_utils import mask_rtsp_url

IPS = ["192.168.46.23", "192.168.41.129", "192.168.44.206", "192.168.43.22", "192.168.2.76", "192.168.7.20", "192.168.41.90"]

async def probe(url, t=8):
    raw = url
    if raw.startswith("ffmpeg:"):
        raw = raw[7:].split("#")[0]
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
        "-i", raw, "-frames:v", "1", "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=t)
    except asyncio.TimeoutError:
        proc.kill(); await proc.wait(); return False, "timeout"
    return proc.returncode==0, (err or b"").decode("utf-8","replace").strip()[:160]

async def main():
    for ip in IPS:
        cam = await camera_collection.find_one({"ip_address": ip})
        if not cam:
            print(ip, "NOT FOUND"); continue
        proto = cam.get("protocol")
        for key in ("sub_rtsp_url", "main_rtsp_url"):
            url = cam.get(key) or ""
            ok, detail = await probe(url)
            print(f"{ip} {proto} {key} ok={ok} {mask_rtsp_url(url)} | {detail}")

asyncio.run(main())
