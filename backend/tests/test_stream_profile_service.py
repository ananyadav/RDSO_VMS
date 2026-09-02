import unittest

from app.services.stream_profile_service import (
    hik_fps_from_api,
    hik_fps_to_api,
    parse_encoder_fields,
    replace_tag,
    resolve_stream_driver,
    supports_stream_profile,
    _fps_options,
    _resolution_options,
)


class TestStreamProfileService(unittest.TestCase):
    def test_supports_hikvision_family(self):
        self.assertTrue(supports_stream_profile("HIKVISION"))
        self.assertTrue(supports_stream_profile("PRAMA"))
        self.assertTrue(supports_stream_profile("ONVIF"))
        self.assertTrue(supports_stream_profile("DAHUA"))
        self.assertEqual(resolve_stream_driver("HIKVISION"), "isapi")
        self.assertEqual(resolve_stream_driver("DAHUA"), "onvif")

    def test_hik_fps_roundtrip(self):
        self.assertEqual(hik_fps_from_api("1500"), 15.0)
        self.assertEqual(hik_fps_from_api("2500"), 25.0)
        self.assertEqual(hik_fps_from_api("100"), 1.0)
        self.assertEqual(hik_fps_from_api("15"), 15.0)
        self.assertEqual(hik_fps_to_api(15), "1500")
        self.assertEqual(hik_fps_to_api(1), "100")
        self.assertEqual(hik_fps_to_api(25), "2500")
        self.assertEqual(hik_fps_to_api(30), "2500")  # clamped

    def test_parse_encoder_fields(self):
        xml = """
        <StreamingChannel>
          <videoCodecType>H.265</videoCodecType>
          <videoResolutionWidth>1920</videoResolutionWidth>
          <videoResolutionHeight>1080</videoResolutionHeight>
          <maxFrameRate>2500</maxFrameRate>
        </StreamingChannel>
        """
        fields = parse_encoder_fields(xml)
        self.assertEqual(fields["videoCodecType"], "H.265")
        self.assertEqual(fields["videoResolutionWidth"], "1920")
        self.assertEqual(hik_fps_from_api(fields["maxFrameRate"]), 25.0)

    def test_replace_tag(self):
        xml = "<maxFrameRate>1500</maxFrameRate>"
        out = replace_tag(xml, "maxFrameRate", "2000")
        self.assertIn("2000", out)
        self.assertNotIn("1500", out)

    def test_fps_options_default_range(self):
        opts = _fps_options("", 12.0)
        self.assertEqual(opts[0], 1)
        self.assertEqual(opts[-1], 25)
        self.assertIn(12, opts)

    def test_resolution_options_includes_current(self):
        opts = _resolution_options("", "1280", "720")
        self.assertTrue(any(o["width"] == 1280 and o["height"] == 720 for o in opts))


if __name__ == "__main__":
    unittest.main()
