"""Camera sequence validation constants."""

NAME_MAX_LEN = 120
DESCRIPTION_MAX_LEN = 500
MIN_CAMERAS = 2
DWELL_MIN_SECONDS = 2
DWELL_MAX_SECONDS = 300
DWELL_DEFAULT_SECONDS = 10

FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "password",
        "username",
        "rtsp_url",
        "main_rtsp_url",
        "sub_rtsp_url",
        "recording_rtsp_url",
        "credentials",
        "secret",
        "token",
        "api_key",
    }
)
