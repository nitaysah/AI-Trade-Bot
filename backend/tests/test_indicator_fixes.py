"""Regression tests for the eleven indicator fixes from the indicator audit (INDICATOR_AUDIT.md).

Each test compares the app with an independent reference formula or with the documented rule, on completed bars only."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import indicators as I  # noqa: E402


def make_frame(n=500, seed=7, drift=0.0, noise=1.0, freq="B", start=None):
    rng = np.random.default_rng(seed)
    start = start if start is not None else (100 + 400 * max(0.0, -drift))  # keeps steep downtrends above zero
    close = start + np.cumsum(rng.normal(drift, noise, n))
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.1, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0.4, 0.1, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.4, 0.1, n))
    volume = rng.integers(1000, 5000, n).astype(float)
    index = pd.date_range("2024-01-02", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=index)


def analyse(df, indicators, timeframe="1Day", **settings):
    return I.calculate_indicators_for_df(df.copy(), timeframe=timeframe, ticker="TEST",
                                         settings={"enabled_indicators": list(indicators), **settings})


def extension_columns(df, indicators, **params):
    return I._add_standard_extension_columns(df.copy(), df["Close"], df["High"], df["Low"], df["Volume"],
                                             lambda key, fallback=None: params.get(key, fallback),
                                             enabled_indicators=set(indicators))


def votes(latest, prev=None, **params):
    prev = latest if prev is None else prev
    return I._generate_standard_extension_signals(pd.Series(latest), pd.Series(prev), lambda key, fallback=None: params.get(key, fallback))


class IchimokuTests(unittest.TestCase):
    def test_the_vote_uses_the_cloud_drawn_at_the_current_bar(self):
        for seed in (1, 2, 3, 4, 5):
            df = make_frame(300, seed=seed, drift=0.05)
            out = extension_columns(df, ["Ichimoku Cloud"])
            self.assertTrue(np.allclose(out["Ichimoku_Cloud_A"].dropna(), out["Ichimoku_Span_A"].shift(26).dropna()))
            self.assertTrue(np.allclose(out["Ichimoku_Chikou_Ref"].dropna(), df["Close"].shift(26).dropna()))
            result = analyse(df, ["Ichimoku Cloud"])
            row = out.iloc[-1]
            top, bottom = max(row.Ichimoku_Cloud_A, row.Ichimoku_Cloud_B), min(row.Ichimoku_Cloud_A, row.Ichimoku_Cloud_B)
            close = row.Close
            bull = close > top and row.Ichimoku_Conversion > row.Ichimoku_Base and row.Ichimoku_Span_A > row.Ichimoku_Span_B and close > row.Ichimoku_Chikou_Ref
            bear = close < bottom and row.Ichimoku_Conversion < row.Ichimoku_Base and row.Ichimoku_Span_A < row.Ichimoku_Span_B and close < row.Ichimoku_Chikou_Ref
            expected = "BULLISH" if bull else "BEARISH" if bear else "NEUTRAL"
            self.assertEqual(result["signals"]["Ichimoku Cloud"]["signal"], expected, seed)

    def test_a_clean_trend_votes_with_the_trend_and_no_history_is_neutral(self):
        up = analyse(make_frame(300, seed=9, drift=0.8, noise=0.3), ["Ichimoku Cloud"])
        down = analyse(make_frame(300, seed=9, drift=-0.8, noise=0.3), ["Ichimoku Cloud"])
        self.assertEqual(up["signals"]["Ichimoku Cloud"]["signal"], "BULLISH")
        self.assertEqual(down["signals"]["Ichimoku Cloud"]["signal"], "BEARISH")
        short = analyse(make_frame(40, seed=9, drift=0.8, noise=0.3), ["Ichimoku Cloud"])
        self.assertEqual(short["signals"]["Ichimoku Cloud"]["signal"], "NEUTRAL")


class ChoppinessTests(unittest.TestCase):
    def test_the_vote_does_not_depend_on_keltner_being_selected(self):
        for drift, expected in ((1.0, "BULLISH"), (-1.0, "BEARISH")):
            df = make_frame(300, seed=3, drift=drift, noise=0.15)
            alone = analyse(df, ["Choppiness Index"])["signals"]["Choppiness Index"]["signal"]
            together = analyse(df, ["Choppiness Index", "Keltner Channels"])["signals"]["Choppiness Index"]["signal"]
            self.assertEqual((alone, together), (expected, expected))

    def test_a_ranging_market_is_neutral(self):
        rng = np.random.default_rng(5)
        close = 100 + rng.normal(0, 0.5, 300)  # mean-reverting noise: no net direction, lots of travel
        df = make_frame(300, seed=5)
        df["Close"] = close
        df["High"] = close + 0.3
        df["Low"] = close - 0.3
        df["Open"] = close
        self.assertEqual(analyse(df, ["Choppiness Index"])["signals"]["Choppiness Index"]["signal"], "NEUTRAL")


class OnBalanceVolumeTests(unittest.TestCase):
    def test_matches_granvilles_rule_including_unchanged_closes(self):
        df = make_frame(300, seed=2)
        df.iloc[100:104, df.columns.get_loc("Close")] = df["Close"].iloc[99]  # unchanged closes add nothing
        out = extension_columns(df, ["On-Balance Volume"])
        reference = (np.sign(df["Close"].diff().fillna(0)) * df["Volume"]).cumsum()
        self.assertTrue(np.allclose(out["OBV_Ext"], reference))
        self.assertEqual(out["OBV_Ext"].iloc[0], 0.0)
        self.assertEqual(out["OBV_Ext"].iloc[101], out["OBV_Ext"].iloc[100])


class VwapVoteTests(unittest.TestCase):
    def test_both_sides_are_treated_the_same_way(self):
        above = I._generate_vwap_signal(101.0, 99.0, 100.0, 100.0, False, "VWAP")
        below = I._generate_vwap_signal(99.0, 101.0, 100.0, 100.0, True, "VWAP")
        self.assertEqual((above["signal"], below["signal"]), ("BULLISH", "BEARISH"))
        self.assertIn("breakout", above["reason"])
        self.assertIn("breakdown", below["reason"])
        holding_up = I._generate_vwap_signal(110.0, 109.0, 100.0, 100.0, False, "VWAP")
        holding_down = I._generate_vwap_signal(90.0, 91.0, 100.0, 100.0, False, "VWAP")
        self.assertEqual((holding_up["signal"], holding_down["signal"]), ("BULLISH", "BEARISH"))
        self.assertEqual(I._generate_vwap_signal(100.0, 100.0, float("nan"), 100.0, False, "VWAP")["signal"], "NEUTRAL")
        self.assertEqual(I._generate_vwap_signal(100.0, 100.0, 0.0, 0.0, False, "VWAP")["signal"], "NEUTRAL")


class OscillatorVoteTests(unittest.TestCase):
    def test_ultimate_oscillator_votes_on_the_exhaustion_turn(self):
        self.assertEqual(votes({"Ultimate_Osc": 25.0}, {"Ultimate_Osc": 22.0})["Ultimate Oscillator"]["signal"], "BULLISH")
        self.assertEqual(votes({"Ultimate_Osc": 75.0}, {"Ultimate_Osc": 78.0})["Ultimate Oscillator"]["signal"], "BEARISH")
        # Above 70 and still rising used to be bullish; it is not a buy signal.
        self.assertEqual(votes({"Ultimate_Osc": 75.0}, {"Ultimate_Osc": 72.0})["Ultimate Oscillator"]["signal"], "NEUTRAL")
        self.assertEqual(votes({"Ultimate_Osc": 25.0}, {"Ultimate_Osc": 28.0})["Ultimate Oscillator"]["signal"], "NEUTRAL")

    def test_cmo_votes_on_the_turn_and_needs_a_full_window(self):
        self.assertEqual(votes({"CMO": -60.0}, {"CMO": -65.0})["Chande Momentum Oscillator"]["signal"], "BULLISH")
        self.assertEqual(votes({"CMO": 60.0}, {"CMO": 65.0})["Chande Momentum Oscillator"]["signal"], "BEARISH")
        self.assertEqual(votes({"CMO": 60.0}, {"CMO": 55.0})["Chande Momentum Oscillator"]["signal"], "NEUTRAL")
        out = extension_columns(make_frame(200), ["Chande Momentum Oscillator"])
        self.assertEqual(int(out["CMO"].iloc[:14].notna().sum()), 0)
        self.assertTrue(out["CMO"].iloc[14:].notna().all())
        delta = out["Close"].diff()
        gains = delta.clip(lower=0).rolling(14).sum()
        losses = (-delta.clip(upper=0)).rolling(14).sum()
        self.assertTrue(np.allclose(out["CMO"].dropna(), (100 * (gains - losses) / (gains + losses)).dropna()))

    def test_momentum_votes_on_the_zero_line(self):
        self.assertEqual(votes({"Momentum_Ext": 2.0}, {"Momentum_Ext": 5.0})["Momentum"]["signal"], "BULLISH")
        self.assertEqual(votes({"Momentum_Ext": -2.0}, {"Momentum_Ext": -5.0})["Momentum"]["signal"], "BEARISH")
        self.assertEqual(votes({"Momentum_Ext": 0.0})["Momentum"]["signal"], "NEUTRAL")


class AtrVolatilityTests(unittest.TestCase):
    def test_it_reports_the_volatility_regime_and_never_votes_a_direction(self):
        row = {"Close": 105.0, "ATR_Ext": 3.0, "ATR_Ext_Avg": 2.0}
        for prev_close in (100.0, 110.0):
            result = votes(row, {**row, "Close": prev_close})["ATR Volatility"]
            self.assertEqual(result["signal"], "NEUTRAL")
            self.assertIn("expanding", result["reason"])
        calm = votes({"Close": 105.0, "ATR_Ext": 2.0, "ATR_Ext_Avg": 2.0})["ATR Volatility"]
        self.assertEqual(calm["signal"], "NEUTRAL")
        self.assertIn("normal", calm["reason"])


class AlmaTests(unittest.TestCase):
    def test_matches_the_definition_with_a_floored_offset(self):
        df = make_frame(300, seed=4)
        out = extension_columns(df, ["Arnaud Legoux Moving Average"])
        window, offset, sigma = 9, 0.85, 6.0
        m = np.floor(offset * (window - 1))
        s = window / sigma
        weights = np.exp(-((np.arange(window) - m) ** 2) / (2 * s * s))
        weights /= weights.sum()
        reference = df["Close"].rolling(window).apply(lambda values: np.dot(values, weights), raw=True)
        self.assertTrue(np.allclose(out["ALMA"].dropna(), reference.dropna()))


class MysticPulseTests(unittest.TestCase):
    def test_nothing_is_reported_before_the_wilder_window_is_complete(self):
        df = make_frame(300, seed=11, drift=0.5, noise=0.4)
        result = analyse(df, ["Mystic Pulse"], timeframe="1Day")["df"]  # daily preset: ADX length 5, no pre-smoothing
        self.assertTrue(result["plus_di"].iloc[:5].isna().all())
        self.assertEqual(int((result["Bull_Pulse"].iloc[:6] > 0).sum() + (result["Bear_Pulse"].iloc[:6] > 0).sum()), 0)
        self.assertTrue(result["plus_di"].iloc[6:].notna().all())

    def test_directional_lines_match_wilders_dmi_once_warmed_up(self):
        df = make_frame(300, seed=11, drift=0.5, noise=0.4)
        result = analyse(df, ["Mystic Pulse"], timeframe="1Day")["df"]
        _, plus, minus = I._wilder_dmi_series(df["High"], df["Low"], df["Close"], 5)
        self.assertLess(float((result["plus_di"] - plus).abs().iloc[80:].max()), 1e-3)
        self.assertLess(float((result["minus_di"] - minus).abs().iloc[80:].max()), 1e-3)


def candle_frame(rows):
    index = pd.date_range("2024-01-02", periods=len(rows), freq="B", tz="UTC")
    frame = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=index)
    frame["Volume"] = 1000.0
    I._add_candlestick_columns(frame)
    return frame


def trend(direction, count=14, start=100.0):
    """Candles with a steady one-point body and a 1.6 range, so the average body and range are about 1.0 and 1.6."""
    rows, price = [], start
    for _ in range(count):
        step = -1.0 if direction == "down" else 1.0
        open_, close = price, price + step
        rows.append((open_, max(open_, close) + 0.3, min(open_, close) - 0.3, close))
        price = close
    return rows, price


class CandlePatternTests(unittest.TestCase):
    def test_fast_and_full_paths_share_one_implementation(self):
        self.assertIs(I._add_fast_candlestick_columns, I._add_candlestick_columns)

    def test_bullish_engulfing_needs_a_long_body_and_a_prior_downtrend(self):
        down, price = trend("down")
        engulf = [(price + 1.0, price + 1.2, price - 0.2, price), (price - 0.5, price + 2.8, price - 0.7, price + 2.5)]
        self.assertEqual(candle_frame(down + engulf).iloc[-1][["cdl_name", "cdl_bullish"]].tolist(), ["Bull Engulfing", True])
        up, top = trend("up")
        same_shape_in_an_uptrend = [(top + 1.0, top + 1.2, top - 0.2, top), (top - 0.5, top + 2.8, top - 0.7, top + 2.5)]
        self.assertFalse(candle_frame(up + same_shape_in_an_uptrend).iloc[-1]["cdl_bullish"])
        tiny = [(price + 0.2, price + 0.3, price - 0.1, price), (price - 0.05, price + 0.3, price - 0.2, price + 0.25)]
        self.assertFalse(candle_frame(down + tiny).iloc[-1]["cdl_bullish"])  # smaller than the recent average body

    def test_hammer_is_a_hammer_after_a_decline_and_a_hanging_man_after_a_rise(self):
        down, price = trend("down")
        hammer = [(price, price + 0.55, price - 2.0, price + 0.5)]
        frame = candle_frame(down + hammer)
        self.assertEqual(frame.iloc[-1]["cdl_name"], "Hammer")
        self.assertTrue(frame.iloc[-1]["cdl_bullish"])
        up, top = trend("up")
        frame = candle_frame(up + [(top, top + 0.55, top - 2.0, top + 0.5)])
        self.assertEqual(frame.iloc[-1]["cdl_name"], "Hanging Man")
        self.assertTrue(frame.iloc[-1]["cdl_bearish"])

    def test_morning_star_needs_a_long_first_and_third_candle(self):
        down, price = trend("down")
        first = (price + 2.5, price + 2.6, price - 0.1, price)          # long bearish
        star = (price - 0.4, price - 0.2, price - 0.7, price - 0.5)      # small body
        third = (price - 0.3, price + 2.6, price - 0.4, price + 2.4)     # long bullish, closes above the first body's middle
        frame = candle_frame(down + [first, star, third])
        self.assertEqual(frame.iloc[-1]["cdl_name"], "Morning Star")
        weak_first = (price + 0.4, price + 0.5, price - 0.1, price)      # a short first candle is not a star setup
        self.assertNotEqual(candle_frame(down + [weak_first, star, third]).iloc[-1]["cdl_name"], "Morning Star")

    def test_doji_uses_the_average_range_not_the_current_range(self):
        down, price = trend("down")
        doji = [(price, price + 0.8, price - 0.8, price + 0.05)]
        self.assertEqual(candle_frame(down + doji).iloc[-1]["cdl_name"], "Doji")

    def test_a_bar_is_never_both_bullish_and_bearish(self):
        for seed in range(6):
            df = make_frame(600, seed=seed, noise=1.5)
            I._add_candlestick_columns(df)
            self.assertEqual(int((df["cdl_bullish"] & df["cdl_bearish"]).sum()), 0)
            self.assertTrue((df.loc[df["cdl_bullish"] | df["cdl_bearish"], "cdl_name"] != "Neutral").all())

    def test_no_pattern_fires_before_ten_candles_of_history_exist(self):
        df = make_frame(60, seed=3, noise=2.0)
        I._add_candlestick_columns(df)
        self.assertFalse((df["cdl_bullish"] | df["cdl_bearish"]).iloc[:11].any())


if __name__ == "__main__":
    unittest.main()


class PriorLevelsAndSessionFixTests(unittest.TestCase):
    def test_crypto_session_dates_use_utc_day(self):
        idx = pd.DatetimeIndex(pd.to_datetime(["2024-01-02 23:30", "2024-01-03 00:30"]), tz="UTC")
        df = pd.DataFrame({"Close": [1.0, 2.0]}, index=idx)
        from indicators import _session_dates_for_df
        crypto = list(_session_dates_for_df(df, "BTC/USD"))
        equity = list(_session_dates_for_df(df, "SPY"))
        self.assertNotEqual(crypto[0], crypto[1])      # UTC midnight splits the two bars
        self.assertEqual(equity[0], equity[1])         # Chicago date is still Jan 2 for both

    def test_regular_hours_mask(self):
        from indicators import _regular_hours_mask
        idx = pd.DatetimeIndex(pd.to_datetime(["2024-03-04 08:00", "2024-03-04 09:30", "2024-03-04 15:59", "2024-03-04 16:00"]))
        mask = _regular_hours_mask(pd.DataFrame({"Close": [1, 2, 3, 4]}, index=idx), "SPY")
        self.assertEqual(list(mask), [False, True, True, False])

    def test_daily_bars_make_session_vwap_unavailable(self):
        from indicators import _session_vwap_series
        df = make_frame(30, seed=3)
        df.index = pd.date_range("2024-01-01", periods=30, freq="1D")
        vwap = _session_vwap_series(df, df["High"], df["Low"], df["Close"], df["Volume"], "SPY")
        self.assertTrue(vwap.isna().all())

    def test_intraday_vwap_carries_through_zero_volume(self):
        from indicators import _session_vwap_series
        idx = pd.date_range("2024-03-04 09:30", periods=6, freq="5min")
        df = pd.DataFrame({"High": 11.0, "Low": 9.0, "Close": 10.0, "Volume": [100, 100, 0, 0, 100, 100]}, index=idx)
        vwap = _session_vwap_series(df, df["High"], df["Low"], df["Close"], df["Volume"], "SPY")
        self.assertTrue(vwap.notna().all())
        self.assertAlmostEqual(vwap.iloc[2], vwap.iloc[1])

    def test_supertrend_and_haut_use_wilder_seeded_atr(self):
        from indicators import _wilder_atr
        df = make_frame(60, seed=5)
        atr = _wilder_atr(df["High"], df["Low"], df["Close"], 10)
        self.assertTrue(atr.iloc[:9].isna().all())
        self.assertTrue(np.isfinite(atr.iloc[9]))


class StandardsBatchTests(unittest.TestCase):
    def sig(self, name, latest, prev, **params):
        return votes(latest, prev, **params)[name]["signal"]

    def test_stochastic_cross_needs_the_zone(self):
        mid_cross = ({"Stoch_K": 55, "Stoch_D": 50}, {"Stoch_K": 48, "Stoch_D": 50})
        zone_cross = ({"Stoch_K": 22, "Stoch_D": 20}, {"Stoch_K": 15, "Stoch_D": 18})
        self.assertEqual(self.sig("Stochastic", *mid_cross), "NEUTRAL")
        self.assertEqual(self.sig("Stochastic", *zone_cross), "BULLISH")
        self.assertEqual(self.sig("Stochastic", *mid_cross, STOCH_CROSS_ZONE_ONLY=0), "BULLISH")
        top = ({"Stoch_K": 78, "Stoch_D": 82}, {"Stoch_K": 85, "Stoch_D": 83})
        self.assertEqual(self.sig("Stochastic", *top), "BEARISH")

    def test_stoch_rsi_cross_needs_the_zone(self):
        mid_cross = ({"Stoch_RSI_K": 55, "Stoch_RSI_D": 50}, {"Stoch_RSI_K": 48, "Stoch_RSI_D": 50})
        self.assertEqual(self.sig("Stochastic RSI", *mid_cross), "NEUTRAL")

    def test_roc_dead_band(self):
        row = ({"ROC_Ext": 0.1}, {"ROC_Ext": 0.0})
        self.assertEqual(self.sig("Rate of Change", *row), "BULLISH")
        self.assertEqual(self.sig("Rate of Change", *row, ROC_THRESHOLD_PCT=0.5), "NEUTRAL")

    def test_keltner_votes_on_band_breakouts(self):
        base = {"Close": 100, "Keltner_Mid": 99, "Keltner_Upper": 102, "Keltner_Lower": 96}
        self.assertEqual(self.sig("Keltner Channels", base, base), "NEUTRAL")
        self.assertEqual(self.sig("Keltner Channels", {**base, "Close": 103}, base), "BULLISH")
        self.assertEqual(self.sig("Keltner Channels", {**base, "Close": 95}, base), "BEARISH")
        self.assertEqual(self.sig("Keltner Channels", base, base, KELTNER_VOTE_MODE="midline"), "BULLISH")

    def test_ppo_fast_slow_order_is_enforced(self):
        df = make_frame(200, seed=2)
        good = extension_columns(df, ["Percentage Price Oscillator"], PPO_FAST=12, PPO_SLOW=26)
        swapped = extension_columns(df, ["Percentage Price Oscillator"], PPO_FAST=26, PPO_SLOW=12)
        self.assertTrue(np.allclose(good["PPO_Ext"].dropna(), swapped["PPO_Ext"].dropna()))

    def test_dema_tema_masked_during_warmup(self):
        df = make_frame(200, seed=2)
        out = extension_columns(df, ["DEMA", "TEMA"], DEMA_PERIOD=10, TEMA_PERIOD=10)
        self.assertTrue(out["DEMA"].iloc[:18].isna().all() and np.isfinite(out["DEMA"].iloc[18]))
        self.assertTrue(out["TEMA"].iloc[:27].isna().all() and np.isfinite(out["TEMA"].iloc[27]))

    def test_hma_matches_reference_with_rounded_sqrt(self):
        df = make_frame(200, seed=4)
        out = extension_columns(df, ["Hull Moving Average"], HMA_PERIOD=21)
        def wma(s, n):
            w = np.arange(1, n + 1.0)
            return s.rolling(n).apply(lambda v: np.dot(v, w) / w.sum(), raw=True)
        ref = wma(2 * wma(df["Close"], 10) - wma(df["Close"], 21), 5)  # round(sqrt(21)) = 5 (4.58)
        self.assertTrue(np.allclose(out["HMA"].dropna(), ref.dropna()))


class BollingerEmaPsarTests(unittest.TestCase):
    def param(self, **kw):
        return lambda key, fallback=None: kw.get(key, fallback)

    def test_bollinger_zone_and_modes(self):
        v = I._bollinger_vote
        self.assertEqual(v(90.5, 90, 110, self.param())["signal"], "BULLISH")
        self.assertEqual(v(100, 90, 110, self.param())["signal"], "NEUTRAL")
        self.assertEqual(v(108.5, 90, 110, self.param())["signal"], "BEARISH")
        self.assertEqual(v(108.5, 90, 110, self.param(BOLL_ZONE_PCT=5))["signal"], "NEUTRAL")
        self.assertEqual(v(111, 90, 110, self.param(BOLL_VOTE_MODE="breakout"))["signal"], "BULLISH")
        self.assertEqual(v(89, 90, 110, self.param(BOLL_VOTE_MODE="breakout"))["signal"], "BEARISH")
        self.assertEqual(v(float("nan"), 90, 110, self.param())["signal"], "NEUTRAL")

    def test_ema_cross_buffer_blocks_whipsaw(self):
        kw = dict(average_type="EMA", price=100, primary_value=100.05, previous_primary=99.9, primary_period=9,
                  use_second_line=True, secondary_value=100.0, previous_secondary=100.0, secondary_period=21)
        self.assertEqual(I._generate_moving_average_signal(**kw)["signal"], "BULLISH")
        self.assertEqual(I._generate_moving_average_signal(**kw, separation_pct=0.2)["signal"], "NEUTRAL")

    def test_psar_follows_wilder_rules(self):
        df = make_frame(400, seed=11, drift=0.02)
        sar = I._wilder_psar(df["High"], df["Low"], df["Close"], 0.02, 0.2)
        self.assertTrue(sar.notna().all())
        above = sar > df["Close"]
        flips = above != above.shift(1)
        # on a flip bar the SAR is the prior extreme, i.e. on the other side of price from the bar's close
        self.assertGreater(int(flips.sum()), 3)
        for i in range(2, len(df)):
            if not above.iloc[i] and not flips.iloc[i]:
                self.assertLessEqual(sar.iloc[i], min(df["Low"].iloc[i - 1], df["Low"].iloc[i - 2]) + 1e-9)
            if above.iloc[i] and not flips.iloc[i]:
                self.assertGreaterEqual(sar.iloc[i], max(df["High"].iloc[i - 1], df["High"].iloc[i - 2]) - 1e-9)

    def test_psar_matches_hand_example(self):
        idx = pd.date_range("2024-01-01", periods=4, freq="D")
        high = pd.Series([10.0, 11.0, 12.0, 11.0], index=idx)
        low = pd.Series([9.0, 10.0, 11.0, 8.0], index=idx)
        close = pd.Series([9.5, 10.5, 11.5, 9.0], index=idx)
        sar = I._wilder_psar(high, low, close, 0.02, 0.2)
        # up-trend from bar 0: SAR=9, EP=10. Bars 1 and 2 would give 9.02 / 9.08 but the SAR may not rise above the
        # prior two lows (9 and 9), so it stays at 9.0.
        self.assertAlmostEqual(sar.iloc[1], 9.0)
        self.assertAlmostEqual(sar.iloc[2], 9.0)
        self.assertEqual(sar.iloc[3], 12.0)  # low 8 < SAR: reversal, SAR becomes the prior extreme point


class CandleReliabilityMysticTrendRiderTests(unittest.TestCase):
    def frame_with_last(self, o, h, l, c):
        df = make_frame(40, seed=9)
        i = df.index[-1]
        df.loc[i, ["Open", "High", "Low", "Close"]] = [o, h, l, c]
        return df

    def test_spinning_top_is_named_and_never_votes(self):
        base = make_frame(40, seed=9)
        px = float(base["Close"].iloc[-2])
        df = self.frame_with_last(px, px + 1.0, px - 1.0, px + 0.2)  # small body, wicks both sides
        I._add_candlestick_columns(df)
        self.assertEqual(df["cdl_name"].iloc[-1], "Spinning Top")
        self.assertFalse(df["cdl_bullish"].iloc[-1] or df["cdl_bearish"].iloc[-1])

    def test_reliable_only_is_a_subset(self):
        df = make_frame(400, seed=21)
        a, b = df.copy(), df.copy()
        I._add_candlestick_columns(a)
        I._add_candlestick_columns(b, reliable_only=True)
        self.assertTrue((b["cdl_bullish"] <= a["cdl_bullish"]).all())
        self.assertTrue((b["cdl_bearish"] <= a["cdl_bearish"]).all())

    def test_mystic_30_minute_preset_and_override(self):
        self.assertEqual(I._get_mystic_v2_presets("30Min")["adx"], 12)
        self.assertEqual(I._get_mystic_v2_presets("15Min")["adx"], 14)

    def test_trend_rider_rsi_band_is_configurable(self):
        df = make_frame(200, seed=3, drift=0.05)
        a = extension_columns(df, ["Trend Rider"])
        b = extension_columns(df, ["Trend Rider"], TREND_RIDER_RSI_LOW=0, TREND_RIDER_RSI_HIGH=101, TREND_RIDER_ZONE_STD=3.0)
        self.assertFalse(np.allclose(a["TR_Score"], b["TR_Score"]))
        self.assertTrue((b["TR_Zone_Upper"].dropna() > a["TR_Zone_Upper"].dropna()).all())


class VwapRegularSessionTests(unittest.TestCase):
    def test_regular_only_ignores_extended_hours_volume(self):
        idx = pd.DatetimeIndex(pd.to_datetime(["2024-03-04 08:00", "2024-03-04 09:30", "2024-03-04 09:35"]))
        df = pd.DataFrame({"High": [51.0, 11.0, 11.0], "Low": [49.0, 9.0, 9.0], "Close": [50.0, 10.0, 10.0],
                           "Volume": [10000.0, 100.0, 100.0]}, index=idx)
        full = I._session_vwap_series(df, df["High"], df["Low"], df["Close"], df["Volume"], "SPY")
        rth = I._session_vwap_series(df, df["High"], df["Low"], df["Close"], df["Volume"], "SPY", regular_only=True)
        self.assertGreater(full.iloc[-1], 40)
        self.assertAlmostEqual(rth.iloc[-1], 10.0)
        self.assertTrue(np.isnan(rth.iloc[0]))


class PivotAndPriorDayVariantTests(unittest.TestCase):
    def intraday(self):
        df = make_frame(780, seed=4, freq="5min")
        df.index = pd.date_range("2024-03-04 09:30", periods=780, freq="5min")  # naive ET, three sessions
        return df

    def calc(self, **params):
        return analyse(self.intraday(), ["Pivot Points", "Prior Day High/Low"], timeframe="5Min", **params)["df"]

    def test_fibonacci_levels(self):
        d = self.calc(PIVOT_TYPE="fibonacci").dropna(subset=["Pivot_P"])
        rng = d["Daily_HighLow_High"] - d["Daily_HighLow_Low"]
        self.assertTrue(np.allclose(d["Pivot_R1"], d["Pivot_P"] + 0.382 * rng))
        self.assertTrue(np.allclose(d["Pivot_S3"], d["Pivot_P"] - 1.0 * rng))
        self.assertTrue(d["Pivot_R4"].isna().all())

    def test_camarilla_levels(self):
        d = self.calc(PIVOT_TYPE="camarilla").dropna(subset=["Pivot_P"])
        rng = d["Daily_HighLow_High"] - d["Daily_HighLow_Low"]
        prior_close = 3 * d["Pivot_P"] - d["Daily_HighLow_High"] - d["Daily_HighLow_Low"]
        self.assertTrue(np.allclose(d["Pivot_R3"], prior_close + rng * 1.1 / 4))

    def test_classic_is_unchanged_and_has_extension_levels(self):
        d = self.calc().dropna(subset=["Pivot_P"])
        self.assertTrue(np.allclose(d["Pivot_R1"], 2 * d["Pivot_P"] - d["Daily_HighLow_Low"]))
        self.assertTrue(d["Pivot_R4"].notna().all())

    def test_atr_buffer_widens_the_prior_day_trigger(self):
        plain = self.calc(DAILY_HIGH_LOW_BUFFER_PCT=0.1).dropna(subset=["Daily_HighLow_High"])
        atr = self.calc(DAILY_HIGH_LOW_BUFFER_PCT=0.1, DAILY_HIGH_LOW_BUFFER_ATR=0.5).dropna(subset=["Daily_HighLow_High_Trigger"])
        both = atr["Daily_HighLow_High_Trigger"].iloc[30:]
        self.assertTrue((both > plain["Daily_HighLow_High_Trigger"].loc[both.index]).all())


class AutoAvwapAnchorTests(unittest.TestCase):
    def series(self, df, **params):
        vw = I._session_vwap_series(df, df["High"], df["Low"], df["Close"], df["Volume"], "TEST")
        return I._auto_avwap_series(df, df["High"], df["Low"], df["Close"], df["Volume"], "TEST", vw,
                                    lambda k, f=None: params.get(k, f))

    def test_no_volume_spike_before_a_full_average(self):
        df = make_frame(40, seed=1)
        df.iloc[3, df.columns.get_loc("Volume")] = 10**7  # huge bar inside the first 20 bars
        _, anchors = self.series(df)
        self.assertTrue(anchors.iloc[:19].isna().all())

    def test_session_open_anchor_resets_each_day(self):
        df = make_frame(300, seed=2, freq="5min")
        df.index = pd.date_range("2024-03-04 09:30", periods=300, freq="5min")
        _, anchors = self.series(df, AUTO_AVWAP_ANCHOR="session_open")
        firsts = [0, 78 + 0]  # sessions are calendar days; first bar of a new date re-anchors
        day2 = int(np.argmax(df.index.date != df.index.date[0]))
        self.assertEqual(int(anchors.iloc[day2]), day2)
        self.assertEqual(int(anchors.iloc[day2 - 1]), 0)

    def test_swing_anchor_is_causal(self):
        df = make_frame(200, seed=3)
        a, anchors = self.series(df, AUTO_AVWAP_ANCHOR="swing", AUTO_AVWAP_SWING_STRENGTH=3)
        valid = anchors.dropna()
        self.assertTrue((valid.index.to_series().map(df.index.get_loc) >= valid.astype(int) + 3).all())
        cut = df.iloc[:120]
        a2, _ = self.series(cut, AUTO_AVWAP_ANCHOR="swing", AUTO_AVWAP_SWING_STRENGTH=3)
        self.assertTrue(np.allclose(a.iloc[:120].dropna(), a2.dropna()))


class VolumeProfileTests(unittest.TestCase):
    def test_value_area_uses_two_row_expansion_and_covers_target(self):
        df = make_frame(300, seed=8, freq="5min")
        prof = I._volume_profile_distribution(df, rows=48, value_area_pct=70.0)
        inside = sum(b["volume"] for b in prof["bins"] if b["low"] >= prof["val"] - 1e-9 and b["high"] <= prof["vah"] + 1e-9)
        self.assertGreaterEqual(inside / prof["total_volume"], 0.70 - 1e-9)
        self.assertTrue(prof["val"] <= prof["poc"] <= prof["vah"])

    def test_hand_profile(self):
        idx = pd.date_range("2024-03-04 09:30", periods=4, freq="5min")
        df = pd.DataFrame({"Open": 10.0, "High": [10.5, 10.5, 12.5, 14.5], "Low": [9.5, 9.5, 11.5, 13.5],
                           "Close": 10.0, "Volume": [100.0, 100.0, 40.0, 10.0]}, index=idx)
        prof = I._volume_profile_distribution(df, rows=12, value_area_pct=70.0)
        self.assertLess(prof["poc"], 10.6)          # the heavy volume trades near 10
        self.assertLess(prof["vah"], 13.0)          # thin high-price rows are outside the 70% area


class SwingLiveBacktestParityTests(unittest.TestCase):
    def levels(self, frame, source):
        d = frame.copy()
        I._add_swing_point_columns(d, "SPY", {"enabled_indicators": ["Swing Points"], "source": source},
                                   lambda k, f=None: {"SWING_POINTS_LOOKBACK_DAYS": 2}.get(k, f), "5Min")
        return d.iloc[-1][[c for c in d.columns if c.startswith("Swing_")]]

    def test_live_matches_backtest_with_extended_hours_bars(self):
        idx = []
        for day in pd.bdate_range("2024-02-01", periods=30):
            idx += list(pd.date_range(day + pd.Timedelta(hours=4), periods=192, freq="5min"))  # 04:00-20:00 ET
        rng = np.random.default_rng(5)
        close = 100 + np.cumsum(rng.normal(0, 0.3, len(idx)))
        df = pd.DataFrame({"Open": np.r_[close[0], close[:-1]], "High": close + 0.3, "Low": close - 0.3, "Close": close,
                           "Volume": rng.integers(1000, 5000, len(idx)).astype(float)}, index=pd.DatetimeIndex(idx))
        live, backtest = self.levels(df, "live"), self.levels(df, "backtest")
        pd.testing.assert_series_equal(live, backtest, check_names=False)


class WilderAtrPerformanceTests(unittest.TestCase):
    @staticmethod
    def reference(high, low, close, period):
        """The original Series.iloc implementation, kept as the oracle."""
        length = max(2, int(period))
        previous_close = close.shift(1)
        tr = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()],
                       axis=1).max(axis=1, skipna=True)
        atr = pd.Series(np.nan, index=close.index, dtype=float)
        if len(tr) < length:
            return atr
        seed = tr.iloc[:length].mean()
        if not np.isfinite(seed):
            return atr
        atr.iloc[length - 1] = float(seed)
        for i in range(length, len(tr)):
            cur, prior = tr.iloc[i], atr.iloc[i - 1]
            if not np.isfinite(cur) or not np.isfinite(prior):
                continue
            atr.iloc[i] = ((prior * (length - 1)) + cur) / length
        return atr

    def test_matches_the_original_including_gaps_and_short_series(self):
        for seed, n, period, hole in ((1, 300, 14, None), (2, 50, 14, 30), (3, 10, 14, None), (4, 200, 2, 5), (5, 14, 14, None)):
            df = make_frame(n, seed=seed)
            if hole is not None:
                df.iloc[hole, df.columns.get_loc("High")] = np.nan
                df.iloc[hole, df.columns.get_loc("Low")] = np.nan
                df.iloc[hole, df.columns.get_loc("Close")] = np.nan
            got = I._wilder_atr(df["High"], df["Low"], df["Close"], period)
            want = self.reference(df["High"], df["Low"], df["Close"], period)
            pd.testing.assert_series_equal(got, want, check_exact=False, rtol=1e-12, atol=1e-12)

    def test_is_fast_enough_for_live_scans(self):
        import time
        df = make_frame(1200, seed=9)
        started = time.perf_counter()
        for _ in range(20):
            I._wilder_atr(df["High"], df["Low"], df["Close"], 14)
        self.assertLess((time.perf_counter() - started) / 20, 0.05)  # was ~0.24 s
