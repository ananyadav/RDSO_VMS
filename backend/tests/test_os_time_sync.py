"""RDSO 18.3.6 — OS network time sync status / admin actions."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import make_mocked_request

from app.services.app_timezone import clear_app_timezone_cache, get_system_time_status
from app.services.os_time_sync import (
    SYNC_CONFIGURATION_REQUIRED,
    SYNC_NOT_SYNCHRONIZED,
    SYNC_SERVICE_UNAVAILABLE,
    SYNC_SYNCHRONIZED,
    apply_os_time_action,
    parse_chronyc_tracking,
    parse_timedatectl,
    parse_w32tm_peers,
    parse_w32tm_status,
    probe_os_time_sync,
)


CHRONYC_SYNCED = """
Reference ID    : A9FEA9FE (time.example.com)
Stratum         : 3
Ref time (UTC)  : Mon Sep 07 08:00:00 2026
System time     : 0.000001234 seconds fast of NTP time
Last offset     : +0.000001000 seconds
RMS offset      : 0.000002000 seconds
Frequency       : 5.000 ppm slow
Residual freq   : +0.001 ppm
Skew            : 0.050 ppm
Root delay      : 0.020000 seconds
Root dispersion : 0.001000 seconds
Update interval : 64.0 seconds
Leap status     : Normal
"""

CHRONYC_UNSYNCED = """
Reference ID    : 00000000 ()
Stratum         : 0
Leap status     : Not synchronised
"""

TIMEDATECTL_SYNCED = """
Timezone=Asia/Kolkata
NTP=yes
NTPSynchronized=yes
SystemClockSynchronized=yes
"""

TIMEDATECTL_UNSYNCED = """
NTP=yes
NTPSynchronized=no
SystemClockSynchronized=no
"""

W32TM_SYNCED = """
Leap Indicator: 0(no warning)
Stratum: 3 (secondary reference - syncd by (S)NTP)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0312500s
Root Dispersion: 0.0500000s
ReferenceId: 0x0A0A0A0A (source IP:  10.10.10.10)
Last Successful Sync Time: 9/7/2026 2:00:00 PM
Source: time.windows.com,0x8
Poll Interval: 10 (1024s)
"""

W32TM_LOCAL = """
Leap Indicator: 3(not synchronized)
Stratum: 0 (unspecified)
Last Successful Sync Time: unspecified
Source: Local CMOS Clock
Poll Interval: 10 (1024s)
"""


class ParserTests(unittest.TestCase):
    def test_chronyc_synchronized(self):
        d = parse_chronyc_tracking(CHRONYC_SYNCED)
        self.assertTrue(d["synchronized"])
        self.assertEqual(d["stratum"], 3)
        self.assertIn("time.example.com", d["reference"])

    def test_chronyc_not_synchronized(self):
        d = parse_chronyc_tracking(CHRONYC_UNSYNCED)
        self.assertFalse(d["synchronized"])

    def test_timedatectl_states(self):
        self.assertTrue(parse_timedatectl(TIMEDATECTL_SYNCED)["synchronized"])
        self.assertFalse(parse_timedatectl(TIMEDATECTL_UNSYNCED)["synchronized"])

    def test_w32tm_states(self):
        self.assertTrue(parse_w32tm_status(W32TM_SYNCED)["synchronized"])
        self.assertEqual(parse_w32tm_status(W32TM_SYNCED)["source"], "time.windows.com")
        self.assertFalse(parse_w32tm_status(W32TM_LOCAL)["synchronized"])

    def test_w32tm_peers_strips_flags(self):
        peers = parse_w32tm_peers(
            "#Peers: 1\n\nPeer: time.windows.com,0x9\nState: Pending\nStratum: 0 (unspecified)\n"
        )
        self.assertEqual(peers, ["time.windows.com"])


class ProbeTests(unittest.TestCase):
    def test_linux_chrony_synchronized(self):
        with patch("app.services.os_time_sync.shutil.which", side_effect=lambda c: c == "chronyc"), patch(
            "app.services.os_time_sync._run_cmd",
            side_effect=[
                (0, CHRONYC_SYNCED, ""),
                (0, "^*= time.example.com          2   6    17   +20ms", ""),
            ],
        ), patch(
            "app.services.os_time_sync._detect_chrony_site_server",
            return_value={"capable": True, "active": False, "mode": "chrony_client", "guidance": "x"},
        ):
            st = probe_os_time_sync(force_platform="Linux")
        self.assertEqual(st["service"], "chrony")
        self.assertEqual(st["sync_state"], SYNC_SYNCHRONIZED)
        self.assertTrue(st["synchronized"])

    def test_linux_timesyncd_not_synced(self):
        with patch(
            "app.services.os_time_sync.shutil.which",
            side_effect=lambda c: c == "timedatectl",
        ), patch(
            "app.services.os_time_sync._run_cmd",
            return_value=(0, TIMEDATECTL_UNSYNCED, ""),
        ):
            st = probe_os_time_sync(force_platform="Linux")
        self.assertEqual(st["service"], "systemd-timesyncd")
        self.assertEqual(st["sync_state"], SYNC_NOT_SYNCHRONIZED)
        self.assertFalse(st["site_time_source"]["capable"])

    def test_windows_service_unavailable(self):
        with patch(
            "app.services.os_time_sync._run_cmd",
            return_value=(0, "STATE              : 1  STOPPED", ""),
        ):
            st = probe_os_time_sync(force_platform="Windows")
        self.assertEqual(st["service"], "w32time")
        self.assertIn(st["sync_state"], (SYNC_CONFIGURATION_REQUIRED, SYNC_SERVICE_UNAVAILABLE))
        self.assertFalse(st["synchronized"])


class ActionTests(unittest.TestCase):
    def test_requires_confirm(self):
        out = apply_os_time_action("resync", confirm=False)
        self.assertFalse(out["ok"])
        self.assertIn("confirm", out["error"])

    def test_windows_resync_with_confirm(self):
        with patch("app.services.os_time_sync._run_cmd", return_value=(0, "The command completed successfully.", "")):
            out = apply_os_time_action("resync", confirm=True, force_platform="Windows")
        self.assertTrue(out["ok"])
        self.assertEqual(out["action"], "resync")


class SystemTimeStatusTests(unittest.TestCase):
    def tearDown(self):
        clear_app_timezone_cache()

    def test_status_includes_sync_state_and_no_in_app_server(self):
        with patch(
            "app.services.os_time_sync.probe_os_time_sync",
            return_value={
                "service": "w32time",
                "service_running": True,
                "synchronized": True,
                "sync_state": SYNC_SYNCHRONIZED,
                "ntp_servers": ["time.windows.com"],
                "source": "time.windows.com",
                "site_time_source": {"capable": True, "active": False, "mode": "w32time_ntp", "guidance": "g"},
            },
        ):
            st = get_system_time_status()
        self.assertEqual(st["sync_state"], SYNC_SYNCHRONIZED)
        self.assertFalse(st["ntp"]["in_app_ntp_server"])
        self.assertEqual(st["recording_timestamps"], "utc_iso_from_os_clock")
        self.assertIn("app_timezone", st)

    def test_timezone_unaffected_by_probe(self):
        clear_app_timezone_cache()
        with patch.dict("os.environ", {"APP_TIMEZONE": "UTC"}):
            clear_app_timezone_cache()
            with patch(
                "app.services.os_time_sync.probe_os_time_sync",
                return_value={
                    "service": None,
                    "service_running": False,
                    "synchronized": False,
                    "sync_state": SYNC_SERVICE_UNAVAILABLE,
                    "ntp_servers": [],
                    "site_time_source": {"capable": False, "active": False},
                },
            ):
                st = get_system_time_status()
            self.assertEqual(st["app_timezone"], "UTC")
        clear_app_timezone_cache()


class SystemTimeRouteRbacTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_requires_super_admin(self):
        from app.routes.recording import system_time_endpoint

        req = make_mocked_request("GET", "/api/system/time")
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=MagicMock(status=403),
        ) as denied:
            denied.return_value = MagicMock(status=403)
            # deny_unless returns a Response
            from aiohttp import web

            denied.return_value = web.json_response({"error": "forbidden"}, status=403)
            resp = await system_time_endpoint(req)
        self.assertEqual(resp.status, 403)

    async def test_get_ok_for_super_admin(self):
        from app.routes.recording import system_time_endpoint

        req = make_mocked_request("GET", "/api/system/time")
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.app_timezone.get_system_time_status",
            return_value={"utc_now": "t", "sync_state": SYNC_SYNCHRONIZED, "ntp": {"in_app_ntp_server": False}},
        ):
            resp = await system_time_endpoint(req)
        self.assertEqual(resp.status, 200)

    async def test_post_action_requires_confirm_body(self):
        from app.routes.recording import system_time_action_endpoint

        req = make_mocked_request("POST", "/api/system/time/actions")
        req._payload = None

        async def _json():
            return {"action": "resync", "confirm": False}

        req.json = _json  # type: ignore
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = await system_time_action_endpoint(req)
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
