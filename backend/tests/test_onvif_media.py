import unittest

from app.services.onvif_media import (
    assign_main_sub_profiles,
    parse_encoder_configuration,
    parse_encoder_options,
    parse_profiles_list,
)
from app.services.stream_profile_service import (
    ONVIF_PROFILE_TIMEOUT_SEC,
    resolve_stream_driver,
    supports_stream_profile,
    uses_isapi_stream_profile,
)


SAMPLE_PROFILES = """
<trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
  xmlns:tt="http://www.onvif.org/ver10/schema">
  <trt:Profiles token="Profile_1">
    <tt:Name>mainStream</tt:Name>
    <tt:VideoEncoderConfiguration token="VEC_Main">
      <tt:Encoding>H264</tt:Encoding>
      <tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>
      <tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit></tt:RateControl>
    </tt:VideoEncoderConfiguration>
  </trt:Profiles>
  <trt:Profiles token="Profile_2">
    <tt:Name>subStream</tt:Name>
    <tt:VideoEncoderConfiguration token="VEC_Sub">
      <tt:Encoding>H264</tt:Encoding>
      <tt:Resolution><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:Resolution>
      <tt:RateControl><tt:FrameRateLimit>15</tt:FrameRateLimit></tt:RateControl>
    </tt:VideoEncoderConfiguration>
  </trt:Profiles>
</trt:GetProfilesResponse>
"""

SAMPLE_ENCODER = """
<trt:GetVideoEncoderConfigurationResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
  xmlns:tt="http://www.onvif.org/ver10/schema">
  <trt:Configuration token="VEC_Main">
    <tt:Name>Main</tt:Name>
    <tt:Encoding>H264</tt:Encoding>
    <tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>
    <tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit></tt:RateControl>
  </trt:Configuration>
</trt:GetVideoEncoderConfigurationResponse>
"""

SAMPLE_OPTIONS = """
<trt:GetVideoEncoderConfigurationOptionsResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
  xmlns:tt="http://www.onvif.org/ver10/schema">
  <trt:Options>
    <tt:H264>
      <tt:ResolutionsAvailable><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:ResolutionsAvailable>
      <tt:ResolutionsAvailable><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:ResolutionsAvailable>
      <tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>30</tt:Max></tt:FrameRateRange>
    </tt:H264>
  </trt:Options>
</trt:GetVideoEncoderConfigurationOptionsResponse>
"""


class TestOnvifMediaParsing(unittest.TestCase):
    def test_parse_profiles_list(self):
        profiles = parse_profiles_list(SAMPLE_PROFILES)
        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[0]["encoder_token"], "VEC_Main")
        self.assertEqual(profiles[0]["width"], 1920)
        self.assertEqual(profiles[1]["name"], "subStream")

    def test_assign_main_sub_by_name(self):
        profiles = parse_profiles_list(SAMPLE_PROFILES)
        main, sub = assign_main_sub_profiles(profiles)
        self.assertEqual(main["name"], "mainStream")
        self.assertEqual(sub["name"], "subStream")

    def test_parse_encoder_configuration(self):
        token, fields, elem = parse_encoder_configuration(SAMPLE_ENCODER)
        self.assertEqual(token, "VEC_Main")
        self.assertEqual(fields["width"], 1920)
        self.assertEqual(fields["fps"], 25)
        self.assertIsNotNone(elem)

    def test_parse_encoder_options_clamps_fps(self):
        opts = parse_encoder_options(SAMPLE_OPTIONS)
        self.assertTrue(any(o["width"] == 640 for o in opts["resolutions"]))
        self.assertEqual(opts["fps_options"][-1], 25)
        self.assertIn(15, opts["fps_options"])


class TestStreamDriverDispatch(unittest.TestCase):
    def test_isapi_brands_use_isapi(self):
        for brand in ("HIKVISION", "PRAMA", "HONEYWELL", "SPARSH", "HIK"):
            self.assertEqual(resolve_stream_driver(brand), "isapi")
            self.assertTrue(uses_isapi_stream_profile(brand))

    def test_non_isapi_protocols_try_onvif(self):
        for protocol in ("ONVIF", "DAHUA", "UNIVIEW", "VIVOTEK", "CUSTOM"):
            self.assertEqual(resolve_stream_driver(protocol), "onvif")
            self.assertFalse(uses_isapi_stream_profile(protocol))

    def test_supports_stream_profile_always_true(self):
        self.assertTrue(supports_stream_profile("DAHUA"))
        self.assertTrue(supports_stream_profile("CUSTOM"))
        self.assertTrue(supports_stream_profile("HIKVISION"))

    def test_onvif_timeout_is_short(self):
        self.assertLessEqual(ONVIF_PROFILE_TIMEOUT_SEC, 10)


if __name__ == "__main__":
    unittest.main()
