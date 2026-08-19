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


if __name__ == "__main__":
    unittest.main()
