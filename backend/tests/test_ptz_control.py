import unittest

from app.services.onvif_ptz import _direction_velocity, _envelope
from app.services.ptz_control import backends_for


class TestPtzControlDispatch(unittest.TestCase):
    def test_onvif_protocol_tries_onvif_before_isapi(self):
        self.assertEqual(
            tuple(backends_for({"protocol": "ONVIF", "ptz": True})),
            ("onvif", "isapi"),
        )

    def test_custom_protocol_is_not_hikvision_only(self):
        self.assertEqual(
            tuple(backends_for({"protocol": "CUSTOM", "ptz": True})),
            ("onvif", "isapi"),
        )

    def test_hikvision_prefers_isapi(self):
        self.assertEqual(
            tuple(backends_for({"protocol": "HIKVISION"})),
            ("isapi", "onvif"),
        )

    def test_dahua_prefers_cgi(self):
        self.assertEqual(
            tuple(backends_for({"protocol": "DAHUA"})),
            ("dahua", "onvif"),
        )


class TestOnvifPtzHelpers(unittest.TestCase):
    def test_direction_velocity(self):
        self.assertEqual(_direction_velocity("right", 2), (0.6, 0.0, 0.0))
        self.assertEqual(_direction_velocity("up", 3), (0.0, 1.0, 0.0))

    def test_envelope_contains_digest_token(self):
        xml = _envelope("admin", "secret", "<tptz:Stop/>").decode()
        self.assertIn("UsernameToken", xml)
        self.assertIn("PasswordDigest", xml)
        self.assertIn("<tptz:Stop/>", xml)

    def test_envelope_can_use_password_text(self):
        xml = _envelope("admin", "secret", "<tptz:Stop/>", password_text=True).decode()
        self.assertIn("PasswordText", xml)
        self.assertIn("secret", xml)

    def test_rewrite_xaddr_keeps_camera_ip_and_onvif_path(self):
        from app.services.onvif_ptz import _rewrite_xaddr

        url = _rewrite_xaddr(
            {"ip_address": "192.168.11.31", "http_port": 80},
            "http://127.0.0.1/onvif/media",
        )
        self.assertEqual(url, "http://192.168.11.31/onvif/media")

    def test_xaddrs_from_capabilities(self):
        from app.services.onvif_ptz import _xaddrs_from_capabilities

        xml = """
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                    xmlns:tt="http://www.onvif.org/ver10/schema">
          <s:Body>
            <tt:Capabilities>
              <tt:Media><tt:XAddr>http://192.168.11.30/onvif/media</tt:XAddr></tt:Media>
              <tt:PTZ><tt:XAddr>http://192.168.11.30/onvif/ptz</tt:XAddr></tt:PTZ>
            </tt:Capabilities>
          </s:Body>
        </s:Envelope>
        """
        addrs = _xaddrs_from_capabilities(xml)
        self.assertEqual(addrs["media"], "http://192.168.11.30/onvif/media")
        self.assertEqual(addrs["ptz"], "http://192.168.11.30/onvif/ptz")


if __name__ == "__main__":
    unittest.main()
