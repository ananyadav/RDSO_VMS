
import asyncio, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(r"C:\\Users\\Ananya Yadav\\Cursor Workspace\\CCTV")
sys.path.insert(0, str(ROOT / 'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from app.core.database import camera_collection
IPS = ["192.168.41.31","192.168.46.49","192.168.9.36","192.168.41.33","192.168.44.170","192.168.8.71","192.168.41.59","192.168.44.125","192.168.7.76","192.168.41.69","192.168.44.65","192.168.41.50","192.168.44.66","192.168.43.18","192.168.41.17","192.168.44.110","192.168.41.35","192.168.44.111","192.168.41.208","192.168.44.112","192.168.7.136","192.168.41.79"]
async def main():
    now = datetime.now(timezone.utc).isoformat()
    for ip in IPS:
        await camera_collection.update_one(
            {'ip_address': ip},
            {'$set': {
                'stream_health_ok': True,
                'stream_health_alarm': False,
                'stream_health_strikes': 0,
                'stream_health_category': 'online',
                'stream_health_message': '',
                'stream_health_checked_at': now,
            }},
        )
    print('marked', len(IPS))
asyncio.run(main())
