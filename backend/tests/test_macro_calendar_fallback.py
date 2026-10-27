"""Macro calendar build in main: feed-outage fallback and the impact filter for official schedule entries.

All network access is mocked; history goes to a temporary directory."""

import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import macro_schedule as ms
import main


def _feed_response(events):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = events
    return response


def _feed_down(*_args, **_kwargs):
    raise TimeoutError("feed timeout")


def _official(title, family, impact, when):
    return ms._make_event(title, when, impact, family, "BLS")


class MacroCalendarFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._saved_cache = dict(main._macro_calendar_cache)
        self.addCleanup(lambda: main._macro_calendar_cache.update(self._saved_cache))
        main._macro_calendar_cache.update({"data": [], "timestamp": 0})
        self.today = datetime.now(ms.EASTERN).replace(hour=8, minute=30, second=0, microsecond=0)
        patches = [
            patch.object(main, "GENERIC_DASHBOARD_CARD_CACHE_DIR", self._dir.name),
            patch.object(main, "enrich_macro_calendar_events", side_effect=lambda events: events),
            patch("macro_service.get_macro_data", return_value={}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def _build(self, feed, official):
        with patch("requests.get", side_effect=feed), \
                patch.object(ms, "fetch_official_events", return_value=(official, {"BLS": "ok"})):
            return (await main._fetch_macro_events_uncached())["calendar"]

    def _feed_ok(self, events):
        return lambda url, **_kw: _feed_response(events if "thisweek" in url else [])

    async def test_feed_outage_keeps_the_last_good_calendar_instead_of_official_only_rows(self):
        cpi = {"title": "Core CPI m/m", "country": "USD", "impact": "High", "date": self.today.isoformat(), "forecast": "0.3%"}
        good = await self._build(self._feed_ok([cpi]), [])
        self.assertEqual([e["title"] for e in good], ["Core CPI m/m"])
        main._macro_calendar_cache["timestamp"] = 0                     # hour elapsed: the next call refetches
        official = [_official("Consumer Price Index (CPI)", "cpi", "High", self.today + timedelta(days=1))]
        out = await self._build(_feed_down, official)
        self.assertEqual([e["title"] for e in out], ["Core CPI m/m"])  # no official-only rebuild
        self.assertEqual(main._macro_calendar_cache["timestamp"], 0)  # not marked fresh, so the feed is retried

    async def test_cold_start_outage_serves_history_without_saving_and_retries_soon(self):
        stored = [{"title": "Retail Sales m/m", "country": "USD", "impact": "High", "date": (self.today - timedelta(days=1)).isoformat()}]
        path = f"{self._dir.name}/macro_calendar_history.json"
        ms.save_history(path, stored)
        out = await self._build(_feed_down, [_official("Producer Price Index (PPI)", "ppi", "High", self.today + timedelta(days=2))])
        self.assertEqual(sorted(e["title"] for e in out), ["Producer Price Index (PPI)", "Retail Sales m/m"])
        self.assertEqual([e["title"] for e in ms.load_history(path)], ["Retail Sales m/m"])  # partial view not persisted
        age = datetime.now().timestamp() - main._macro_calendar_cache["timestamp"]
        self.assertGreater(age, 3000)                                   # expires in about 5 minutes, not an hour

    async def test_a_rescheduled_official_release_loses_its_old_date_end_to_end(self):
        path = f"{self._dir.name}/macro_calendar_history.json"
        old = _official("Producer Price Index (PPI)", "ppi", "High", self.today + timedelta(days=2))
        new = _official("Producer Price Index (PPI)", "ppi", "High", self.today + timedelta(days=4))
        ms.save_history(path, [old])
        out = await self._build(self._feed_ok([]), [new])                 # BLS status "ok" in _build
        self.assertEqual([e["date"] for e in out if e["family"] == "ppi"], [new["date"]])
        self.assertEqual([e["date"] for e in ms.load_history(path)], [new["date"]])

    async def test_low_impact_official_releases_are_not_shown_or_kept(self):
        official = [_official("Real Earnings", "bls_real_earnings", "Low", self.today + timedelta(days=1)),
                    _official("JOLTS Job Openings", "jolts", "Medium", self.today + timedelta(days=1))]
        out = await self._build(self._feed_ok([]), official)
        self.assertEqual([e["title"] for e in out], ["JOLTS Job Openings"])
        saved = ms.load_history(f"{self._dir.name}/macro_calendar_history.json")
        self.assertEqual([e["title"] for e in saved], ["JOLTS Job Openings"])


if __name__ == "__main__":
    unittest.main()
