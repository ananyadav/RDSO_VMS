"""Playback calendar-day timezone boundary tests (APP_TIMEZONE)."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.services.app_timezone import (
    clear_app_timezone_cache,
    local_day_bounds_utc,
    local_month_bounds_utc,
)
from app.services.playback_search import (
    _dates_for_interval,
    _interval_overlaps_day,
    _parse_date,
)


class _TzEnvMixin:
    def _set_tz(self, name: str) -> None:
        self._prev = os.environ.get("APP_TIMEZONE")
        os.environ["APP_TIMEZONE"] = name
        clear_app_timezone_cache()

    def _restore_tz(self) -> None:
        if self._prev is None:
            os.environ.pop("APP_TIMEZONE", None)
        else:
            os.environ["APP_TIMEZONE"] = self._prev
        clear_app_timezone_cache()


class TestLocalDayBoundsUtc(unittest.TestCase, _TzEnvMixin):
    def tearDown(self) -> None:
        self._restore_tz()

    def test_utc_day_bounds(self):
        """F. UTC calendar days when APP_TIMEZONE=UTC."""
        self._set_tz("UTC")
        start, end = local_day_bounds_utc("2026-09-04")
        self.assertEqual(start, datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc))

    def test_kolkata_day_bounds(self):
        self._set_tz("Asia/Kolkata")
        start, end = local_day_bounds_utc("2026-09-04")
        self.assertEqual(start, datetime(2026, 9, 3, 18, 30, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 4, 18, 30, tzinfo=timezone.utc))

    def test_kolkata_month_bounds(self):
        self._set_tz("Asia/Kolkata")
        start, end = local_month_bounds_utc(2026, 9)
        self.assertEqual(start, datetime(2026, 8, 31, 18, 30, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 30, 18, 30, tzinfo=timezone.utc))


class TestPlaybackDateClassification(unittest.TestCase, _TzEnvMixin):
    def tearDown(self) -> None:
        self._restore_tz()

    def test_a_midday_recording_in_local_day(self):
        """A. Mid-day local recording is included."""
        self._set_tz("Asia/Kolkata")
        day_start, day_end = _parse_date("2026-09-04")
        # 2026-09-04 12:00 IST = 2026-09-04 06:30 UTC
        mid = datetime(2026, 9, 4, 6, 30, tzinfo=timezone.utc)
        self.assertTrue(_interval_overlaps_day(mid, mid, day_start, day_end))

    def test_b_after_local_midnight_on_previous_utc_date(self):
        """B. Shortly after local midnight belongs to new local date (UTC still previous day)."""
        self._set_tz("Asia/Kolkata")
        # 2026-09-04 00:30 IST = 2026-09-03 19:00 UTC
        instant = datetime(2026, 9, 3, 19, 0, tzinfo=timezone.utc)
        day_start, day_end = _parse_date("2026-09-04")
        self.assertTrue(_interval_overlaps_day(instant, instant, day_start, day_end))
        prev_start, prev_end = _parse_date("2026-09-03")
        self.assertFalse(_interval_overlaps_day(instant, instant, prev_start, prev_end))

    def test_c_before_local_midnight_stays_on_local_date(self):
        """C. Shortly before local midnight stays on that local date."""
        self._set_tz("Asia/Kolkata")
        # 2026-09-04 23:30 IST = 2026-09-04 18:00 UTC
        instant = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)
        day_start, day_end = _parse_date("2026-09-04")
        self.assertTrue(_interval_overlaps_day(instant, instant, day_start, day_end))
        next_start, next_end = _parse_date("2026-09-05")
        self.assertFalse(_interval_overlaps_day(instant, instant, next_start, next_end))

    def test_d_outside_next_local_day_excluded(self):
        """D. Instant at next local midnight is excluded from previous day (half-open)."""
        self._set_tz("Asia/Kolkata")
        # 2026-09-05 00:00 IST = 2026-09-04 18:30 UTC
        boundary = datetime(2026, 9, 4, 18, 30, tzinfo=timezone.utc)
        day_start, day_end = _parse_date("2026-09-04")
        self.assertFalse(_interval_overlaps_day(boundary, boundary, day_start, day_end))
        next_start, next_end = _parse_date("2026-09-05")
        self.assertTrue(_interval_overlaps_day(boundary, boundary, next_start, next_end))

    def test_e_dates_helper_matches_search_day_classification(self):
        """E. Month date classification agrees with /search day bounds."""
        self._set_tz("Asia/Kolkata")
        # After local midnight on 4th (UTC still 3rd)
        after_midnight = datetime(2026, 9, 3, 19, 0, tzinfo=timezone.utc)
        dates = _dates_for_interval(after_midnight, after_midnight, 2026, 9)
        self.assertEqual(dates, {"2026-09-04"})

        day_start, day_end = _parse_date("2026-09-04")
        self.assertTrue(_interval_overlaps_day(after_midnight, after_midnight, day_start, day_end))

    def test_f_utc_midday_unchanged(self):
        """F. With APP_TIMEZONE=UTC, midday UTC stays on that UTC date."""
        self._set_tz("UTC")
        mid = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
        day_start, day_end = _parse_date("2026-09-04")
        self.assertTrue(_interval_overlaps_day(mid, mid, day_start, day_end))
        self.assertEqual(day_start.day, 4)
        self.assertEqual(day_start.hour, 0)

    def test_dst_transition_uses_zoneinfo(self):
        """DST: America/New_York spring-forward day still has correct half-open bounds."""
        self._set_tz("America/New_York")
        # 2026-03-08 is DST start in US; local midnight still maps via zoneinfo
        start, end = local_day_bounds_utc("2026-03-08")
        self.assertEqual(start.tzinfo, timezone.utc)
        self.assertEqual(end.tzinfo, timezone.utc)
        # Day length in UTC is 23 hours on spring-forward
        hours = (end - start).total_seconds() / 3600
        self.assertEqual(hours, 23.0)


class TestParseDateUsesAppTimezone(unittest.TestCase, _TzEnvMixin):
    def tearDown(self) -> None:
        self._restore_tz()

    def test_parse_date_delegates_to_local_bounds(self):
        self._set_tz("Asia/Kolkata")
        self.assertEqual(_parse_date("2026-09-04"), local_day_bounds_utc("2026-09-04"))


class TestEffectiveTimezoneName(unittest.TestCase, _TzEnvMixin):
    def tearDown(self) -> None:
        self._restore_tz()

    def test_invalid_name_falls_back_to_utc_for_clients(self):
        self._set_tz("Not/A_Real_Zone")
        from app.services.app_timezone import get_effective_app_timezone_name

        self.assertEqual(get_effective_app_timezone_name(), "UTC")

    def test_valid_kolkata_exposed(self):
        self._set_tz("Asia/Kolkata")
        from app.services.app_timezone import get_effective_app_timezone_name

        self.assertEqual(get_effective_app_timezone_name(), "Asia/Kolkata")


if __name__ == "__main__":
    unittest.main()
