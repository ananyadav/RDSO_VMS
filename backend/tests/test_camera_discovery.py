import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.camera_discovery import (
    dedupe_discovered,
    discover_cameras_full,
    extract_ip_from_url,
    is_onvif_network_video_transmitter,
    mark_discovery_status,
    normalize_discovery_ip,
    parse_onvif_scopes,
    parse_wsdiscovery_service,
    scan_subnet_for_cameras,
    subnets_from_camera_ips,
    validate_scan_cidr,
)


class TestOnvifScopeParsing(unittest.TestCase):
    def test_parse_name_hardware_manufacturer(self):
        scopes = [
            "onvif://www.onvif.org/name/Front Door Cam",
            "onvif://www.onvif.org/hardware/DS-2CD2347G2-LU",
            "onvif://www.onvif.org/manufacturer/Hikvision",
        ]
        meta = parse_onvif_scopes(scopes)
        self.assertEqual(meta["name"], "Front Door Cam")
        self.assertEqual(meta["model"], "DS-2CD2347G2-LU")
        self.assertEqual(meta["manufacturer"], "Hikvision")

    def test_hardware_with_brand_prefix(self):
        scopes = ["onvif://www.onvif.org/hardware/DAHUA IPC-HFW1230S"]
        meta = parse_onvif_scopes(scopes)
        self.assertEqual(meta["model"], "DAHUA IPC-HFW1230S")
        self.assertEqual(meta["manufacturer"], "DAHUA")


class TestWsDiscoveryParsing(unittest.TestCase):
    def test_network_video_transmitter_type(self):
        svc = MagicMock()
        svc.getTypes.return_value = [
            "{http://www.onvif.org/ver10/network/wsdl}NetworkVideoTransmitter"
        ]
        svc.getXAddrs.return_value = ["http://192.168.41.95:80/onvif/device_service"]
        svc.getScopes.return_value = [
            "onvif://www.onvif.org/name/Cam95",
            "onvif://www.onvif.org/hardware/DS-2CD",
            "onvif://www.onvif.org/manufacturer/Hikvision",
        ]
        row = parse_wsdiscovery_service(svc)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["ip_address"], "192.168.41.95")
        self.assertEqual(row["onvif_endpoint"], "http://192.168.41.95:80/onvif/device_service")
        self.assertEqual(row["manufacturer"], "Hikvision")
        self.assertEqual(row["model"], "DS-2CD")
        self.assertEqual(row["name"], "Cam95")

    def test_rejects_non_onvif_computer(self):
        svc = MagicMock()
        svc.getTypes.return_value = [
            "http://schemas.xmlsoap.org/ws/2006/02/devprof:Device",
            "http://schemas.microsoft.com/windows/pub/2005/07:Computer",
        ]
        svc.getXAddrs.return_value = ["http://192.168.50.15:5357/uuid/"]
        svc.getScopes.return_value = []
        self.assertIsNone(parse_wsdiscovery_service(svc))

    def test_onvif_scope_without_nvt_type(self):
        svc = MagicMock()
        svc.getTypes.return_value = ["http://schemas.xmlsoap.org/ws/2006/02/devprof:Device"]
        svc.getXAddrs.return_value = ["http://10.0.0.5/onvif/device_service"]
        svc.getScopes.return_value = ["onvif://www.onvif.org/name/Gate Cam"]
        row = parse_wsdiscovery_service(svc)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["ip_address"], "10.0.0.5")


class TestDiscoveryHelpers(unittest.TestCase):
    def test_extract_ip(self):
        self.assertEqual(extract_ip_from_url("http://192.168.1.10:80/onvif/device_service"), "192.168.1.10")

    def test_dedupe_prefers_richer_row(self):
        rows = dedupe_discovered(
            [
                {"ip_address": "10.0.0.1", "name": "Cam", "manufacturer": "", "model": "", "onvif_endpoint": ""},
                {
                    "ip_address": "10.0.0.1",
                    "name": "Cam",
                    "manufacturer": "Hikvision",
                    "model": "DS-2CD",
                    "onvif_endpoint": "http://10.0.0.1/onvif/device_service",
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["manufacturer"], "Hikvision")

    def test_mark_status(self):
        rows = mark_discovery_status(
            [{"ip_address": "10.0.0.1", "name": "A"}, {"ip_address": "10.0.0.2", "name": "B"}],
            {"10.0.0.2"},
        )
        self.assertEqual(rows[0]["status"], "new")
        self.assertEqual(rows[1]["status"], "already_added")

    def test_is_onvif_nvt_by_endpoint_path(self):
        self.assertTrue(
            is_onvif_network_video_transmitter([], [], ["http://192.168.1.5/onvif/device_service"])
        )


class TestSubnetValidation(unittest.TestCase):
    def test_valid_slash_24(self):
        net = validate_scan_cidr("192.168.41.0/24")
        self.assertEqual(str(net), "192.168.41.0/24")

    def test_reject_invalid(self):
        with self.assertRaises(ValueError):
            validate_scan_cidr("not-a-cidr")

    def test_reject_slash_23(self):
        with self.assertRaises(ValueError) as ctx:
            validate_scan_cidr("192.168.40.0/23")
        self.assertIn("too large", str(ctx.exception).lower())

    def test_subnets_from_ips(self):
        subnets = subnets_from_camera_ips(
            ["192.168.41.95", "192.168.44.10", "192.168.41.200", "bad"]
        )
        self.assertEqual(subnets, ["192.168.41.0/24", "192.168.44.0/24"])


class TestSubnetScanStatus(unittest.IsolatedAsyncioTestCase):
    async def test_existing_ip_marked_already_added(self):
        configured = {normalize_discovery_ip("192.168.41.5")}
        with patch(
            "app.services.camera_discovery._probe_rtsp_open",
            new_callable=AsyncMock,
            side_effect=lambda ip, **_: ip == "192.168.41.5",
        ), patch(
            "app.services.camera_discovery.probe_onvif_metadata",
            new_callable=AsyncMock,
            return_value={"manufacturer": "", "model": "", "onvif_endpoint": ""},
        ):
            rows = await scan_subnet_for_cameras(
                "192.168.41.0/28",
                configured_ips=configured,
            )
        by_ip = {r["ip_address"]: r["status"] for r in rows}
        self.assertEqual(by_ip.get("192.168.41.5"), "already_added")

    async def test_unknown_ip_marked_new(self):
        with patch(
            "app.services.camera_discovery._probe_rtsp_open",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.services.camera_discovery.probe_onvif_metadata",
            new_callable=AsyncMock,
            return_value={"manufacturer": "Hikvision", "model": "DS-2CD", "onvif_endpoint": "http://x/onvif/device_service"},
        ):
            rows = await scan_subnet_for_cameras(
                "192.168.41.0/30",
                configured_ips=set(),
            )
        self.assertTrue(rows)
        self.assertEqual(rows[0]["status"], "new")
        self.assertEqual(rows[0]["manufacturer"], "Hikvision")

    async def test_discover_full_merges_ws_and_subnet(self):
        configured = set()
        with patch(
            "app.services.camera_discovery.discover_onvif_cameras",
            new_callable=AsyncMock,
            return_value=[
                {
                    "ip_address": "10.0.0.5",
                    "name": "WS Cam",
                    "manufacturer": "",
                    "model": "",
                    "onvif_endpoint": "http://10.0.0.5/onvif/device_service",
                    "status": "new",
                }
            ],
        ), patch(
            "app.services.camera_discovery.scan_subnet_for_cameras",
            new_callable=AsyncMock,
            return_value=[
                {
                    "ip_address": "192.168.41.95",
                    "name": "Subnet Cam",
                    "manufacturer": "",
                    "model": "",
                    "onvif_endpoint": "",
                    "status": "new",
                }
            ],
        ):
            result = await discover_cameras_full(configured_ips=configured, subnet="192.168.41.0/24")
        ips = {r["ip_address"] for r in result["discovered"]}
        self.assertIn("10.0.0.5", ips)
        self.assertIn("192.168.41.95", ips)
        self.assertEqual(result["subnet_scanned"], "192.168.41.0/24")


if __name__ == "__main__":
    unittest.main()
