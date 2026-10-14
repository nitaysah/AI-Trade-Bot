"""Scan Log and log rows for bots running a strategy; the manual re-entry rules do not apply to them."""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import engine
import main
import nyse_calendar
import strategy_presets as sp
import strategy_scan_log as sl

ET = ZoneInfo("America/New_York")
CANDLE_TS = int(datetime(2026, 10, 6, tzinfo=ET).timestamp())


def setUpModule():
    global _cal
    _cal = patch.object(nyse_calendar, "_calendar", return_value=None)
    _cal.start()


def tearDownModule():
    _cal.stop()


GC = {"strategy_preset_enabled": True, "strategy_preset": "golden_cross", "strategy_preset_variant": "sma_50_200",
      "buy_mode": sp.PRESET_MODE, "sell_mode": sp.PRESET_MODE, "timeframe": "1Day", "market_session": "Regular",
      "enable_cooldown": False, "enable_signal_reset": False, "enable_smart_reentry": False}
ST = {**GC, "strategy_preset": "supertrend_ema200", "strategy_preset_variant": "classic_10_3"}


def gc_info(**extra):
    info = {"status": "READY", "action": "HOLD", "state": "FLAT", "signal_age_bars": 19, "atr": 2.0, "stop_atr_mult": 3.0,
            "values": {"close": 100.0, "fast": 410.2, "slow": 421.7}, "warmup": {"required": 202, "available": 640}}
    info.update(extra)
    return info


def st_info(**extra):
    info = {"status": "READY", "action": "HOLD", "state": "LONG", "signal_age_bars": 5, "atr": 2.0, "stop_atr_mult": 3.0,
            "values": {"close": 134.8, "st": 128.5, "st_dir": 1.0, "ema": 124.0}, "warmup": {"required": 602, "available": 900}}
    info.update(extra)
    return info


def scan(info, action="HOLD", reason="no signal", **extra):
    base = {"time": "2026-10-07T10:00:00-04:00", "action": action, "reason": reason, "price_raw": 100.0, "strategy_preset": info,
            "candle_meta": {"candle_key": f"1DAY:{CANDLE_TS}", "candle_status": "closed"}}
    base.update(extra)
    return base


POSITION = {"symbol": "SPY", "qty": 5.0, "avg_price": 90.0, "current_price": 100.0, "unrealized_pl": 50.0}


class StrategyNumbersTests(unittest.TestCase):
    def test_golden_cross_numbers_use_the_variants_own_names(self):
        self.assertEqual([(m["label"], m["value"]) for m in sl.metrics(GC, gc_info())],
                         [("SMA50", "410.20"), ("SMA200", "421.70"), ("Gap", "-2.73%")])
        ema = {**GC, "strategy_preset_variant": "ema_50_200"}
        self.assertEqual(sl.metrics(ema, gc_info())[0]["label"], "EMA50")
        fast = {**GC, "strategy_preset_variant": "sma_20_50"}
        self.assertEqual([m["label"] for m in sl.metrics(fast, gc_info())][:2], ["SMA20", "SMA50"])

    def test_supertrend_numbers(self):
        rows = {m["label"]: m for m in sl.metrics(ST, st_info())}
        self.assertEqual(rows["Supertrend"]["value"], "128.50 bullish")
        self.assertEqual(rows["EMA200"]["value"], "124.00")
        self.assertEqual(rows["Close vs EMA200"]["value"], "+8.71%")

    def test_compact_chip_for_log_rows(self):
        self.assertEqual(sl.compact(GC, gc_info()), "SMA50 410.20 < SMA200 421.70 (-2.73%)")
        self.assertIn("ST bullish", sl.compact(ST, st_info()))
        self.assertEqual(sl.compact(GC, gc_info(status="WAITING_DATA", values={}, warmup={"required": 202, "available": 150})), "150/202 bars")
        self.assertEqual(sl.compact(GC, {}), "")

    def test_missing_or_garbage_values_never_raise(self):
        for values in ({"close": None, "fast": "x", "slow": float("nan")}, {"fast": 1.0}, {}):
            sl.metrics(GC, gc_info(values=values))
            sl.compact(GC, gc_info(values=values))
            sl.metrics(ST, st_info(values=values))


class StatusAndHeadlineTests(unittest.TestCase):
    def view(self, settings, info, position=None, action="HOLD", policy=None, blockers=(), reason="no signal"):
        return sl.scan_view(settings, scan(info, action, reason), position, policy, list(blockers))

    def test_flat_waiting_for_the_cross(self):
        view = self.view(GC, gc_info())
        self.assertEqual(view["status"], "WATCHING")
        self.assertEqual(view["headline"], "Flat · waiting for SMA50 to cross above SMA200.")
        self.assertEqual(view["strategy"], "Golden Cross · Classic 50/200 SMA")
        self.assertEqual(view["timing"]["candle"], "2026-10-06")

    def test_trend_already_up_explains_why_the_bot_is_not_buying(self):
        view = self.view(ST, st_info())
        self.assertIn("only enters on a fresh signal", view["headline"])
        self.assertIn("5 bars", view["headline"])

    def test_waiting_for_data_shows_the_bar_counts_not_a_normal_wait(self):
        info = gc_info(status="WAITING_DATA", values={}, warmup={"required": 202, "available": 150})
        view = self.view(GC, info)
        self.assertEqual(view["status"], "WAITING_DATA")
        self.assertIn("150/202 completed bars", view["headline"])

    def test_degraded_data_says_entries_are_blocked_but_the_stop_is_still_watched(self):
        view = self.view(GC, gc_info(status="DEGRADED", reason="Prices could not be verified as split-adjusted."))
        self.assertEqual(view["status"], "DEGRADED")
        self.assertIn("protective stop is still watched", view["headline"])

    def test_entry_ready_and_blocked_entry(self):
        self.assertEqual(self.view(GC, gc_info(action="BUY"), action="BUY")["status"], "ENTRY_READY")
        held = self.view(GC, gc_info(action="BUY"), action="HOLD", blockers=["Waiting for the regular session to open (09:30 ET)."])
        self.assertEqual(held["status"], "BLOCKED")
        self.assertIn("gate is holding the order back", held["headline"])
        self.assertNotIn("Trend is up", held["headline"])

    def test_idle_when_the_scan_policy_says_nothing_is_due(self):
        policy = {"idle": True, "next_evaluation_at": "2026-10-08T09:30:00-04:00"}
        view = self.view(GC, gc_info(), policy=policy)
        self.assertEqual(view["status"], "IDLE")
        self.assertEqual(view["timing"]["next_evaluation_at"], "2026-10-08T09:30:00-04:00")
        self.assertIsNone(self.view(GC, gc_info(), policy={"idle": False, "next_evaluation_at": "x"})["timing"]["next_evaluation_at"])

    def test_holding_shows_the_exit_rule_and_the_protective_stop(self):
        settings = {**GC, "strategy_preset_stop": {"stop_atr_mult": 3.0, "entry_atr": 2.0}}
        view = self.view(settings, gc_info(state="LONG"), POSITION)
        self.assertEqual(view["status"], "HOLDING")
        self.assertIn("sells when SMA50 falls below SMA200", view["headline"])
        position = view["position"]
        self.assertEqual((position["entry"], position["current"], position["pl_pct"]), ("90.00", "100.00", 11.11))
        self.assertEqual(position["stop"], "84.00")                      # 90 - 3 x 2.0 (entry ATR), anchored to the average cost
        self.assertEqual(position["stop_distance_pct"], 19.05)
        self.assertIn("not a broker order", position["stop_note"])

    def test_exit_signal(self):
        view = self.view(GC, gc_info(action="SELL", state="FLAT"), POSITION, action="SELL")
        self.assertEqual(view["status"], "EXIT_SIGNAL")
        self.assertIn("sells at the next trading window", view["headline"])

    def test_manual_or_options_bots_get_no_strategy_view(self):
        self.assertIsNone(sl.scan_view({"buy_mode": "indicator"}, scan(gc_info()), None, None, []))
        self.assertIsNone(sl.scan_view({**GC, "options_trading": True}, scan(gc_info()), None, None, []))

    def test_bad_input_degrades_to_text_not_an_exception(self):
        self.assertIsNotNone(sl.scan_view(GC, {"strategy_preset": "garbage", "candle_meta": None}, {"avg_price": "x"}, None, []))
        self.assertIsNone(sl.row_view(GC, {"strategy_preset": None}))


class RowViewTests(unittest.TestCase):
    def test_event_labels_replace_indicator_counts(self):
        cases = [
            ({"action": "BUY", "reason": "entry", "strategy_preset": gc_info(action="BUY")}, "ENTRY"),
            ({"action": "SELL", "reason": "Stop Loss Hit ($93.50 <= $94.00)", "strategy_preset": gc_info()}, "STOP EXIT"),
            ({"action": "SELL", "reason": "Golden Cross: exit condition met", "strategy_preset": gc_info(action="SELL")}, "STRATEGY EXIT"),
            ({"action": "HOLD", "reason": "x", "strategy_preset": gc_info(status="WAITING_DATA")}, "WAITING DATA"),
            ({"action": "HOLD", "reason": "x", "strategy_preset": gc_info(status="DEGRADED")}, "DEGRADED"),
            ({"action": "HOLD", "reason": "window closed", "strategy_preset": gc_info(action="BUY")}, "HELD BACK"),
            ({"action": "HOLD", "reason": "x", "strategy_preset": gc_info()}, "WATCHING"),
        ]
        for trade, event in cases:
            self.assertEqual(sl.row_view(GC, trade)["event"], event, trade)

    def test_format_trade_for_ui_attaches_the_strategy_view_only_for_strategy_bots(self):
        from user_config import UserConfig, set_user_config
        config = UserConfig("log-test")
        config.TICKER_SETTINGS = {"SPY": dict(GC), "QQQ": {"buy_mode": "indicator"}}
        set_user_config(config)
        trade = {"ticker": "SPY", "action": "HOLD", "reason": "x", "timeframe": "1Day", "strategy_preset": gc_info()}
        self.assertEqual(main._format_trade_for_ui(trade)["strategy"]["event"], "WATCHING")
        self.assertIsNone(main._format_trade_for_ui({**trade, "ticker": "QQQ"})["strategy"])


class ReentryRulesAreOffTests(unittest.TestCase):
    def engine(self, settings):
        broker = MagicMock()
        with patch("engine.create_broker", return_value=broker):
            eng = engine.UserEngine("log-engine", MagicMock())
        eng.config.TRADELIST = ["SPY"]
        eng.config.TICKER_SETTINGS = {"SPY": settings}
        eng.get_stock_market_status = lambda session="Regular": {
            "is_active_window": True, "is_open": True, "seconds_to_open": 0, "seconds_to_pre_open": 0, "next_open_str": ""}
        return eng

    def test_a_strategy_bot_has_no_signal_reset_lock(self):
        eng = self.engine(dict(GC))
        self.assertIsNone(eng._set_signal_reset_lock("SPY", "BUY"))
        self.assertIsNone(eng._get_signal_reset_lock("SPY"))
        eng.signal_locked_direction["SPY"] = "BUY"                         # a stale lock from before the strategy was enabled
        self.assertIsNone(eng._get_signal_reset_lock("SPY"))
        manual = self.engine({"buy_mode": "indicator"})
        self.assertEqual(manual._set_signal_reset_lock("SPY", "BUY"), "BUY")  # manual bots are unchanged
        self.assertEqual(manual._get_signal_reset_lock("SPY"), "BUY")

    def build(self, settings, eng, info, position=None):
        formatted = main._format_scan_for_ui(scan(info))
        return main._build_scan_log("SPY", formatted, settings, position or {}, [], {"equity": 10000}, {}, eng, [position] if position else [])

    def test_no_cooldown_signal_reset_or_smart_reentry_blocker_for_a_strategy_bot(self):
        legacy = {**GC, "enable_cooldown": True, "enable_signal_reset": True, "enable_smart_reentry": True}  # saved before the rule existed
        eng = self.engine(legacy)
        eng.signal_locked_direction["SPY"] = "BUY"
        eng.last_trade_timestamps["SPY"] = eng.get_now()                   # just traded: a manual bot would show a cooldown
        log = self.build(legacy, eng, gc_info())
        self.assertEqual(log["blockers"], [])
        self.assertEqual(log["status"], "WATCHING")
        self.assertNotIn("Signal Reset", log["summary"] + log["detail"])
        self.assertNotIn("Cooldown", log["summary"] + log["detail"])
        self.assertEqual(log["strategy_log"]["status"], "WATCHING")

    def test_the_same_state_still_blocks_a_manual_bot(self):
        manual = {"buy_mode": "indicator", "sell_mode": "indicator", "timeframe": "1Day", "enable_cooldown": True,
                  "enable_signal_reset": True, "indicators": ["RSI"], "min_buy_signals": 1}
        eng = self.engine(manual)
        eng.signal_locked_direction["SPY"] = "BUY"
        eng.last_trade_timestamps["SPY"] = eng.get_now()
        log = main._build_scan_log("SPY", main._format_scan_for_ui({"time": "2026-10-07T10:00:00-04:00", "action": "HOLD", "price_raw": 100.0}),
                                   manual, {}, [], {"equity": 10000}, {}, eng, [])
        self.assertTrue(any("Cooldown" in b for b in log["blockers"]))
        self.assertTrue(any("Signal Reset" in b for b in log["blockers"]))
        self.assertIsNone(log["strategy_log"])

    def test_a_gate_that_held_back_the_strategy_is_shown_as_the_blocker(self):
        eng = self.engine(dict(GC))
        formatted = main._format_scan_for_ui(scan(gc_info(action="BUY"), action="HOLD", reason="Waiting for the regular session to open (09:30 ET)."))
        log = main._build_scan_log("SPY", formatted, dict(GC), {}, [], {"equity": 10000}, {}, eng, [])
        self.assertEqual(log["status"], "BLOCKED")
        self.assertEqual(log["blockers"], ["Waiting for the regular session to open (09:30 ET)."])

    def test_a_holding_bot_reports_holding_with_the_stop(self):
        settings = {**GC, "strategy_preset_stop": {"stop_atr_mult": 3.0, "entry_atr": 2.0}}
        eng = self.engine(settings)
        log = self.build(settings, eng, gc_info(state="LONG"), POSITION)
        self.assertEqual(log["status"], "HOLDING")
        self.assertEqual(log["strategy_log"]["position"]["stop"], "84.00")


class CollapseRepeatedRowsTests(unittest.TestCase):
    def row(self, event="WATCHING", ticker="SPY", reason="Golden Cross: waiting", chip="SMA50 1 < SMA200 2 (-1.00%)", **extra):
        row = {"ticker": ticker, "reason": reason, "strategy": {"event": event, "chip": chip}}
        row.update(extra)
        return row

    def test_identical_watching_rows_collapse_into_the_newest_with_a_count(self):
        rows = [self.row(time="10:03"), self.row(time="10:02"), self.row(time="10:01")]
        out = main._collapse_repeated_strategy_rows(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["time"], out[0]["repeat_count"]), ("10:03", 3))

    def test_a_change_in_numbers_reason_ticker_or_event_breaks_the_run(self):
        rows = [self.row(), self.row(chip="SMA50 2 < SMA200 2 (0.00%)"), self.row(reason="other"), self.row(ticker="QQQ"),
                self.row(event="ENTRY"), self.row(event="ENTRY"), self.row(event="WAITING DATA"), self.row(event="WAITING DATA")]
        out = main._collapse_repeated_strategy_rows(rows)
        self.assertEqual(len(out), len(rows))                       # nothing merged: only identical WATCHING rows ever are
        self.assertTrue(all("repeat_count" not in r for r in out))

    def test_manual_bot_rows_are_never_touched(self):
        rows = [{"ticker": "SPY", "reason": "x", "strategy": None}, {"ticker": "SPY", "reason": "x", "strategy": None}]
        self.assertEqual(len(main._collapse_repeated_strategy_rows(rows)), 2)
        self.assertEqual(main._collapse_repeated_strategy_rows([]), [])


class CardSetupAndClosedMarketTests(unittest.TestCase):
    def engine(self, settings, active=True):
        broker = MagicMock()
        with patch("engine.create_broker", return_value=broker):
            eng = engine.UserEngine("card-engine", MagicMock())
        eng.config.TRADELIST = ["SPY"]
        eng.config.TICKER_SETTINGS = {"SPY": settings}
        eng.get_stock_market_status = lambda session="Regular": {
            "is_active_window": active, "is_open": active, "market_session_label": "Regular",
            "seconds_to_open": 0 if active else 40000, "seconds_to_pre_open": 0 if active else 39880, "next_open_str": "Friday at 9:30 AM ET"}
        return eng

    def test_setup_descriptor_names_the_strategy_or_the_entry_and_exit_setups(self):
        self.assertEqual(main._scan_setup_info(GC, True)["kind"], "strategy")
        self.assertEqual(main._scan_setup_info(GC, True)["strategy"], "Golden Cross · Classic 50/200 SMA")
        manual = main._scan_setup_info({"buy_mode": "swing_points", "sell_mode": "hybrid_atr"}, False)
        self.assertEqual(manual, {"kind": "manual", "entry": "Swing Points", "exit": main._exit_strategy_label("hybrid_atr")})
        self.assertEqual(main._scan_setup_info({"ai_autopilot": True}, False), {"kind": "manual", "entry": "AI Autopilot", "exit": "AI Autopilot"})
        self.assertEqual(main._entry_strategy_label("strategy_preset"), "Strategy")      # never the raw sentinel name
        self.assertNotIn("Preset", main._exit_strategy_label("strategy_preset"))

    def test_the_last_scan_stays_visible_while_the_market_is_closed(self):
        eng = self.engine(dict(GC), active=False)
        raw_last = scan(gc_info(), reason="Golden Cross: waiting")
        closed = main._format_scan_for_ui({"time": "2026-10-07T20:00:00-04:00", "is_stock_market_closed": True, "status": "MARKET_CLOSED", "last_real_scan": raw_last})
        log = main._build_scan_log("SPY", closed, dict(GC), {}, [], {"equity": 10000}, {}, eng, [])
        self.assertEqual(log["status"], "MARKET_CLOSED")
        self.assertEqual(log["setup"]["kind"], "strategy")
        self.assertEqual(log["last_scan"]["status"], "WATCHING")
        self.assertEqual(log["last_scan"]["strategy_log"]["headline"], "Flat · waiting for SMA50 to cross above SMA200.")
        self.assertEqual(log["last_scan"]["setup"]["kind"], "strategy")

    def test_a_closed_market_without_a_previous_scan_is_just_the_banner(self):
        eng = self.engine(dict(GC), active=False)
        log = main._build_scan_log("SPY", main._format_scan_for_ui({"time": "t"}), dict(GC), {}, [], {"equity": 10000}, {}, eng, [])
        self.assertEqual(log["status"], "MARKET_CLOSED")
        self.assertIsNone(log["last_scan"])

    def test_a_broken_last_scan_never_breaks_the_closed_banner(self):
        eng = self.engine(dict(GC), active=False)
        closed = main._format_scan_for_ui({"time": "t", "last_real_scan": {"strategy_preset": "garbage", "price_raw": "x"}})
        log = main._build_scan_log("SPY", closed, dict(GC), {}, [], {"equity": 10000}, {}, eng, [])
        self.assertEqual(log["status"], "MARKET_CLOSED")

    def test_a_manual_bots_closed_log_also_keeps_its_last_scan_and_setup(self):
        manual = {"buy_mode": "indicator", "sell_mode": "indicator", "timeframe": "15Min", "indicators": ["RSI"], "min_buy_signals": 1}
        eng = self.engine(manual, active=False)
        closed = main._format_scan_for_ui({"time": "t", "last_real_scan": {"time": "t0", "action": "HOLD", "price_raw": 10.0, "reason": "waiting"}})
        log = main._build_scan_log("SPY", closed, manual, {}, [], {"equity": 10000}, {}, eng, [])
        self.assertEqual(log["status"], "MARKET_CLOSED")
        self.assertEqual(log["setup"], {"kind": "manual", "entry": "Indicator", "exit": main._exit_strategy_label("indicator")})
        self.assertEqual(log["last_scan"]["decision"], "HOLD")
        self.assertEqual(log["last_scan"]["blockers"], [])                       # "market closed" is the banner's job, not a blocker
        self.assertNotEqual(log["last_scan"]["status"], "BLOCKED")

    def test_strategy_forecasts_name_the_strategy_and_are_never_served_from_an_old_snapshot(self):
        settings = {**GC, "forecast_entry_snapshot": {"text": "Strategy: Strategy Preset", "settings_hash": "x"}}
        text, dirty = main._ensure_entry_forecast_snapshot(settings, {}, "SPY")
        self.assertFalse(dirty)
        self.assertIn("Golden Cross · Classic 50/200 SMA", text)
        self.assertNotIn("Strategy Preset", text)
        self.assertIn("Buys when", text)
        exit_text, _ = main._ensure_exit_forecast_snapshot({**GC, "strategy_preset_stop": {"entry_atr": 2.0}}, {}, {"symbol": "SPY", "avg_price": 90.0, "qty": 5}, "SPY")
        self.assertIn("Sells when", exit_text)
        self.assertIn("Protective stop 3x ATR", exit_text)
        self.assertNotIn("Strategy Preset", exit_text)

    def test_scan_view_carries_the_strategy_rules(self):
        view = sl.scan_view(GC, scan(gc_info()), None, None, [])
        self.assertIn("crosses above", view["rules"]["entry"])
        self.assertIn("below", view["rules"]["exit"])
        self.assertIn("not a broker order", view["rules"]["stop"])


class UntestedSettingsTests(unittest.TestCase):
    def test_the_view_flags_settings_the_strategy_was_not_designed_for(self):
        self.assertEqual(sl.scan_view(GC, scan(gc_info()), None, None, [])["untested"], "")
        view = sl.scan_view({**GC, "timeframe": "15Min", "market_session": "Extended"}, scan(gc_info()), None, None, [])
        self.assertIn("15Min", view["untested"])
        self.assertIn("Extended", view["untested"])


class ExitWatchTests(unittest.TestCase):
    """Open position: every exit the strategy has, its real distance, and how near it is (0-100)."""
    FAST20 = {**GC, "strategy_preset_variant": "sma_20_50", "strategy_preset_stop": {"stop_atr_mult": 3.0, "entry_atr": 1.0633}}
    HELD = {"symbol": "SPY", "qty": 5.0, "avg_price": 751.44, "current_price": 751.27, "unrealized_pl": -0.85}

    def rows(self, settings, info, position):
        view = sl.scan_view(settings, scan(info), position, None, [])
        return view, {r["kind"] + ":" + r["label"]: r for r in view["exit_watch"]}

    def test_golden_cross_gap_and_stop_use_the_real_numbers(self):
        info = gc_info(state="LONG", values={"close": 751.27, "fast": 750.67, "slow": 750.30})
        view, rows = self.rows(self.FAST20, info, self.HELD)
        gap = rows["strategy:SMA20 vs SMA50"]
        self.assertEqual(gap["value"], "+0.05% · exits at 0%")
        self.assertEqual(gap["near_pct"], 98)  # a cross is almost due: the bar is nearly full
        stop = rows["stop:Protective stop"]
        self.assertEqual(stop["value"], "748.25 · 0.4% below price")
        self.assertEqual(stop["near_pct"], 5)  # 5% of the way from entry 751.44 to the stop
        self.assertIn("not a broker order", stop["detail"])
        self.assertEqual([r["kind"] for r in view["exit_watch"]], ["strategy", "stop"])

    def test_distance_chips_move_into_the_exit_rows_while_holding(self):
        info = gc_info(state="LONG", values={"close": 751.27, "fast": 750.67, "slow": 750.30})
        held = [m["label"] for m in sl.scan_view(self.FAST20, scan(info), self.HELD, None, [])["metrics"]]
        flat = [m["label"] for m in sl.scan_view(self.FAST20, scan(info), None, None, [])["metrics"]]
        self.assertEqual(held, ["SMA20", "SMA50"])
        self.assertEqual(flat, ["SMA20", "SMA50", "Gap"])
        st_held = [m["label"] for m in sl.scan_view(ST, scan(st_info()), self.HELD, None, [])["metrics"]]
        self.assertNotIn("Close vs EMA200", st_held)

    def test_gap_far_from_the_cross_is_a_nearly_empty_bar_and_a_met_exit_is_full(self):
        far = gc_info(state="LONG", values={"close": 100.0, "fast": 110.0, "slow": 100.0})
        self.assertEqual(self.rows(self.FAST20, far, self.HELD)[1]["strategy:SMA20 vs SMA50"]["near_pct"], 0)
        met = gc_info(state="LONG", values={"close": 100.0, "fast": 99.0, "slow": 100.0})
        row = self.rows(self.FAST20, met, self.HELD)[1]["strategy:SMA20 vs SMA50"]
        self.assertEqual((row["near_pct"], row["value"]), (100, "-1.00% · exit condition met"))

    def test_supertrend_has_two_exits_plus_the_stop(self):
        view, rows = self.rows({**ST, "strategy_preset_stop": {"stop_atr_mult": 3.0, "entry_atr": 2.0}}, st_info(), POSITION)
        self.assertEqual(set(rows), {"strategy:Supertrend line", "strategy:EMA200", "stop:Protective stop"})
        self.assertEqual(rows["strategy:Supertrend line"]["value"], "128.50 · price 4.67% above")
        self.assertEqual(rows["strategy:EMA200"]["near_pct"], 0)  # price is 8.7% above the EMA: far from that exit
        self.assertEqual(rows["stop:Protective stop"]["near_pct"], 0)  # price is above entry: no progress toward the stop

    def test_stop_progress_grows_as_price_falls_toward_it_and_caps_at_100(self):
        settings = {**GC, "strategy_preset_stop": {"stop_atr_mult": 3.0, "entry_atr": 2.0}}   # stop 84 from entry 90
        near = lambda price: self.rows(settings, gc_info(state="LONG"), {**POSITION, "current_price": price})[1]["stop:Protective stop"]
        self.assertEqual(near(87.0)["near_pct"], 50)
        self.assertEqual(near(83.0)["near_pct"], 100)
        self.assertEqual(near(83.0)["value"], "84.00 · reached")

    def test_nothing_while_flat_and_no_stop_means_no_bar(self):
        self.assertEqual(sl.scan_view(GC, scan(gc_info()), None, None, [])["exit_watch"], [])
        _, rows = self.rows(GC, gc_info(state="LONG"), POSITION)   # no stored entry ATR -> falls back to the scan's ATR
        self.assertIn("stop:Protective stop", rows)
        _, bare = self.rows(GC, gc_info(state="LONG", atr=None, stop_atr_mult=3.0), POSITION)
        self.assertEqual(bare["stop:Protective stop"]["value"], "not recorded yet")
        self.assertIsNone(bare["stop:Protective stop"]["near_pct"])

    def test_session_rider_exit_is_the_scheduled_sell_time(self):
        settings = {"strategy_preset_enabled": True, "strategy_preset": "session_rider", "session_buy_time": "close-5",
                    "session_sell_time": "open+5", "session_state": {"entry_submitted_at": "2026-10-09T15:55:03-04:00"}}
        info = {"kind": "schedule", "status": "READY", "schedule": {"phase": "HOLDING_OVERNIGHT", "next_event": "SELL",
                "next_at": "2026-10-12T09:35:00-04:00", "next_label": "Mon Oct 12, 9:35 AM ET · 8:35 AM CT", "holding_qty": 5}}
        view = sl.scan_view(settings, scan(info), self.HELD, None, [])
        row = view["exit_watch"][0]
        self.assertEqual((row["kind"], row["label"], row["until"], row["since"]),
                         ("time", "Scheduled sell", "2026-10-12T09:35:00-04:00", "2026-10-09T15:55:03-04:00"))
        self.assertIn("no stop-loss", row["detail"])
        self.assertEqual(sl.scan_view(settings, scan(info), None, None, [])["exit_watch"], [])


if __name__ == "__main__":
    unittest.main()
