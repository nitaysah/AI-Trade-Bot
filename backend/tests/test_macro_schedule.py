import os
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone

import macro_schedule as ms

BLS_HTML = """
<table><tr>
<td class="other-month" id="d0930"><p class="day">30</p><p><strong>Job Openings and Labor Turnover Survey<br></strong>August 2026<br>10:00 AM</p></td>
<td id="d1014"><p class="day">14</p><p><strong>Consumer Price Index<br></strong>September 2026<br>08:30 AM</p><p><strong>Real Earnings<br></strong>September 2026<br>08:30 AM</p></td>
<td id="d1016"><p class="day">16</p><p><strong>Import and Export Price Indexes<br></strong>September 2026<br>02:00 PM</p></td>
<td class="holiday" id="d1012"><p class="day">12</p><p><strong>Columbus Day<br></strong>Holiday<br></p></td>
</tr></table>
"""

BEA_ICS = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:GDP (Advance Estimate)\\, 3rd Quarter \r\n 2026\r\n"
    "DTSTART;VALUE=DATE-TIME:20261029T123000Z\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Some Minor Release\r\nDTSTART;VALUE=DATE-TIME:20261029T123000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)

FED = {"events": [
    {"title": "FOMC Press Conference", "time": "2:30 p.m.", "month": "2026-10", "days": "28", "type": "FOMC"},
    {"title": "Speech - Governor Waller", "time": "4:30 a.m.", "month": "2026-10", "days": "8", "type": "Speeches"},
    {"title": "Discussion - Chairman Kevin Warsh", "time": "11:30 a.m.", "month": "2026-10", "days": "15", "type": "Speeches"},
    {"title": "H.15 - Selected Interest Rates", "time": "4:15 p.m.", "month": "2026-10", "days": "1, 2", "type": "Stat"},
    {"title": "Holiday - Columbus Day", "time": "", "month": "2026-10", "days": "12", "type": "Other"},
]}


class ParserTests(unittest.TestCase):
    def test_bls_keeps_market_releases_in_eastern_time_and_skips_other_month_cells(self):
        events = ms.parse_bls_month(BLS_HTML, 2026, 10)
        self.assertEqual([e["title"] for e in events], ["Consumer Price Index (CPI)", "Real Earnings", "Import/Export Prices"])
        self.assertEqual(events[1]["impact"], "Low")  # scheduled but not on the market-moving list
        self.assertTrue(events[0]["date"].startswith("2026-10-14T08:30:00"))
        self.assertTrue(events[2]["date"].startswith("2026-10-16T14:00:00"))
        self.assertTrue(all(e["scheduled_only"] and e["forecast"] == "" for e in events))

    def test_bea_ics_unfolds_lines_and_converts_utc_to_eastern(self):
        events = ms.parse_bea_ics(BEA_ICS)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "GDP")
        self.assertTrue(events[0]["date"].startswith("2026-10-29T08:30:00"))
        self.assertIn("3rd Quarter 2026", events[0]["period"])

    def test_fed_keeps_press_conference_and_chair_only(self):
        events = ms.parse_fed_calendar(FED)
        self.assertEqual([e["title"] for e in events], ["FOMC Press Conference", "Discussion - Chairman Kevin Warsh"])
        self.assertTrue(events[0]["date"].startswith("2026-10-28T14:30:00"))

    def test_fetch_reports_per_source_failure_and_keeps_the_rest(self):
        def fake(url):
            if "bls.gov" in url:
                raise OSError("blocked")
            if "bea.gov" in url:
                return BEA_ICS
            return '{"events": []}'
        events, status = ms.fetch_official_events(date(2026, 10, 27), 7, fetch_text=fake)
        self.assertTrue(status["BLS"].startswith("unavailable"))
        self.assertEqual(status["BEA"], "ok")
        self.assertEqual([e["title"] for e in events], ["GDP", "Unemployment Claims"])

    def test_weekly_claims_are_generated_for_thursdays_in_window(self):
        events = ms.weekly_claims_events(date(2026, 10, 16), date(2026, 10, 23))
        self.assertEqual([e["date"][:16] for e in events], ["2026-10-22T08:30"])
        self.assertEqual(ms.event_family("Unemployment Claims"), "claims")
        primary = [{"title": "Unemployment Claims", "country": "USD", "date": "2026-10-22T08:30:00-04:00"}]
        self.assertEqual(ms.merge_official_into_calendar(primary, events), primary)

    def test_fetch_window_excludes_events_outside_range(self):
        events, _ = ms.fetch_official_events(date(2026, 10, 1), 7, fetch_text=lambda u: BEA_ICS if "bea" in u else '{"events": []}')
        self.assertEqual([e["family"] for e in events], ["claims", "claims"])  # only the two Thursday rule events (Oct 1, Oct 8)


class MergeTests(unittest.TestCase):
    def test_official_event_is_dropped_when_primary_covers_same_family_and_day(self):
        primary = [{"title": "Core CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High", "forecast": "0.3%"}]
        official = ms.parse_bls_month(BLS_HTML, 2026, 10)
        merged = ms.merge_official_into_calendar(primary, official)
        titles = [e["title"] for e in merged]
        self.assertIn("Core CPI m/m", titles)
        self.assertNotIn("Consumer Price Index (CPI)", titles)
        self.assertIn("Import/Export Prices", titles)

    def test_history_keeps_actual_when_fresh_copy_omits_it_and_prunes_old(self):
        now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        old = (now - timedelta(days=20)).isoformat()
        recent = (now - timedelta(days=3)).isoformat()
        stored = [
            {"title": "Retail Sales m/m", "country": "USD", "date": recent, "actual": "0.4%", "actual_source": "Census"},
            {"title": "Ancient", "country": "USD", "date": old},
        ]
        fresh = [{"title": "Retail Sales m/m", "country": "USD", "date": recent, "actual": "", "forecast": "0.2%"}]
        out = ms.merge_history(stored, fresh, now)
        self.assertEqual([e["title"] for e in out], ["Retail Sales m/m"])
        self.assertEqual(out[0]["actual"], "0.4%")
        self.assertEqual(out[0]["forecast"], "0.2%")

    def test_display_window_is_14_days_back_and_7_ahead(self):
        now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        def ev(days):
            return {"title": f"e{days}", "country": "USD", "date": (now + timedelta(days=days)).isoformat()}
        out = ms.prune_stored_for_display([ev(-15), ev(-13), ev(0), ev(7), ev(9)], now)
        self.assertEqual([e["title"] for e in out], ["e-13", "e0", "e7"])


class ReviewFixTests(unittest.TestCase):
    def test_stored_official_copy_is_dropped_once_the_feed_covers_the_release(self):
        # Saved on Oct 30 while Nov 6 was only on the official schedule; the feed publishes the jobs rows later.
        now = datetime(2026, 11, 6, 15, tzinfo=timezone.utc)
        official = ms._make_event("Employment Situation (Jobs Report)", datetime(2026, 11, 6, 8, 30, tzinfo=ms.EASTERN),
                                  "High", "jobs", "BLS")
        fresh = [{"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High"},
                 {"title": "Unemployment Rate", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High"}]
        out = ms.merge_history([official], fresh, now)
        self.assertEqual(sorted(e["title"] for e in out), ["Non-Farm Employment Change", "Unemployment Rate"])

    def test_official_copy_stays_when_the_feed_has_not_published_that_release(self):
        now = datetime(2026, 10, 30, 15, tzinfo=timezone.utc)
        official = ms._make_event("Employment Situation (Jobs Report)", datetime(2026, 11, 6, 8, 30, tzinfo=ms.EASTERN),
                                  "High", "jobs", "BLS")
        other = [{"title": "Core CPI m/m", "country": "USD", "date": "2026-11-06T08:30:00-05:00"}]  # different family
        out = ms.merge_history([official], other, now)
        self.assertIn("Employment Situation (Jobs Report)", [e["title"] for e in out])

    def test_vice_chair_speeches_are_not_labelled_as_the_chair(self):
        payload = {"events": [
            {"title": "Speech - Vice Chairman for Supervision Bowman", "time": "9:00 a.m.", "month": "2026-10", "days": "9", "type": "Speeches"},
            {"title": "Speech - Vice Chair Jefferson", "time": "9:00 a.m.", "month": "2026-10", "days": "9", "type": "Speeches"},
            {"title": "Speech - Chair Kevin Warsh", "time": "1:00 p.m.", "month": "2026-10", "days": "9", "type": "Speeches"},
        ]}
        self.assertEqual([e["title"] for e in ms.parse_fed_calendar(payload)], ["Speech - Chair Kevin Warsh"])

    def test_user_agent_carries_no_hard_coded_address(self):
        with open(ms.__file__, encoding="utf-8") as handle:
            self.assertNotIn("@gmail.com", handle.read())
        self.assertTrue(ms.USER_AGENT.startswith("BotBulls/"))


class SecondPassTests(unittest.TestCase):
    # ── #4 holiday-week claims ──
    def test_claims_move_to_wednesday_when_thursday_is_a_federal_holiday(self):
        cases = {date(2026, 11, 26): "2026-11-25", date(2025, 12, 25): "2025-12-24", date(2025, 6, 19): "2025-06-18",
                 date(2024, 7, 4): "2024-07-03"}
        for thursday, release in cases.items():
            events = ms.weekly_claims_events(thursday - timedelta(days=6), thursday)
            self.assertEqual([e["date"][:10] for e in events], [release], thursday)

    def test_a_holiday_thursday_just_past_the_window_still_puts_its_wednesday_release_inside(self):
        events = ms.weekly_claims_events(date(2026, 11, 19), date(2026, 11, 25))
        self.assertEqual([e["date"][:10] for e in events], ["2026-11-19", "2026-11-25"])

    def test_a_feed_claims_release_anywhere_in_the_week_replaces_the_rule_entry(self):
        rule = ms.weekly_claims_events(date(2026, 12, 28), date(2026, 12, 31))   # Thu Dec 31 (not a holiday)
        feed = [{"title": "Unemployment Claims", "country": "USD", "date": "2026-12-30T08:30:00-05:00"}]
        self.assertEqual(ms.merge_official_into_calendar(feed, rule), feed)
        self.assertEqual(ms.drop_covered_official(feed + rule), feed)          # same for entries already in history

    # ── #3 rescheduled / cancelled releases ──
    NOW = datetime(2026, 11, 9, 15, tzinfo=timezone.utc)

    def _cpi(self, day):
        return ms._make_event("Consumer Price Index (CPI)", datetime(2026, 11, day, 8, 30, tzinfo=ms.EASTERN), "High", "cpi", "BLS")

    def test_a_rescheduled_official_release_drops_the_old_date(self):
        window = {"BLS": (date(2026, 11, 9), date(2026, 11, 16))}
        out = ms.merge_history([self._cpi(12)], [self._cpi(14)], self.NOW, coverage=window)
        self.assertEqual([e["date"][:10] for e in out], ["2026-11-14"])

    def test_the_old_date_is_kept_when_that_source_did_not_fetch_or_the_date_is_outside_its_window(self):
        self.assertEqual(len(ms.merge_history([self._cpi(12)], [self._cpi(14)], self.NOW, coverage={})), 2)
        narrow = {"BLS": (date(2026, 11, 9), date(2026, 11, 11))}
        self.assertEqual(len(ms.merge_history([self._cpi(12)], [self._cpi(14)], self.NOW, coverage=narrow)), 2)

    def test_past_entries_are_never_dropped_for_being_absent(self):
        past = self._cpi(5)
        window = {"BLS": (date(2026, 11, 1), date(2026, 11, 16))}
        self.assertEqual(ms.merge_history([past], [], self.NOW, coverage=window), [past])

    def test_a_retimed_feed_event_replaces_the_old_time_only_within_the_days_the_feed_listed(self):
        old = {"title": "Retail Sales m/m", "country": "USD", "date": "2026-11-12T08:30:00-05:00"}
        new = {"title": "Retail Sales m/m", "country": "USD", "date": "2026-11-12T10:00:00-05:00"}
        later = {"title": "Core PCE", "country": "USD", "date": "2026-11-20T08:30:00-05:00"}   # beyond the feed's last day
        coverage = ms.fetch_coverage(date(2026, 11, 9), [new], {"BLS": "ok", "BEA": "stale (cached copy)"})
        self.assertEqual(coverage[ms.FEED_SOURCE], (date(2026, 11, 9), date(2026, 11, 12)))
        self.assertNotIn("BEA", coverage)                                   # a stale copy cannot prove a cancellation
        out = ms.merge_history([old, later], [new], self.NOW, coverage=coverage)
        self.assertEqual([e["date"] for e in out], [new["date"], later["date"]])
        self.assertNotIn(ms.FEED_SOURCE, ms.fetch_coverage(date(2026, 11, 9), None, {}))   # failed feed: no coverage

    # ── #7 parallel fetch and schedule cache ──
    def test_sources_are_fetched_in_parallel(self):
        barrier = threading.Barrier(4, timeout=3)      # 2 BLS months + BEA + Fed must all be in flight at once

        def fake(url):
            barrier.wait()
            return BEA_ICS if "bea" in url else ('{"events": []}' if "federalreserve" in url else "")
        _, status = ms.fetch_official_events(date(2026, 10, 27), 7, fetch_text=fake)
        self.assertEqual(status, {"BLS": "ok", "BEA": "ok", "Federal Reserve": "ok"})

    def test_schedule_text_is_cached_and_a_stale_copy_covers_an_outage(self):
        url, calls = "https://example.invalid/sched-cache-test", []

        def live(u):
            calls.append(u)
            return "v1"
        self.assertEqual(ms.cached_get_text(url, live, now=1000), "v1")
        self.assertEqual(ms.cached_get_text(url, live, now=1000 + 3600), "v1")
        self.assertEqual(len(calls), 1)                                     # within 12h: no second download

        def down(u):
            raise OSError("blocked")
        stale = ms.cached_get_text(url, down, now=1000 + ms.TEXT_CACHE_TTL + 1)
        self.assertIsInstance(stale, ms.StaleText)
        with self.assertRaises(OSError):
            ms.cached_get_text(url, down, now=1000 + ms.TEXT_CACHE_MAX_STALE + 1)

    def test_a_stale_source_is_reported_and_still_contributes_its_events(self):
        def fake(url):
            return ms.StaleText(BEA_ICS) if "bea" in url else '{"events": []}'
        events, status = ms.fetch_official_events(date(2026, 10, 27), 7, fetch_text=fake)
        self.assertEqual(status["BEA"], "stale (cached copy)")
        self.assertIn("GDP", [e["title"] for e in events])


class HistoryFileTests(unittest.TestCase):
    def test_round_trip_and_corrupt_file_is_tolerated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "h.json")
            self.assertEqual(ms.load_history(path), [])
            ms.save_history(path, [{"title": "x"}])
            self.assertEqual(ms.load_history(path), [{"title": "x"}])
            with open(path, "w") as f:
                f.write("{not json")
            self.assertEqual(ms.load_history(path), [])
            self.assertEqual([n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
