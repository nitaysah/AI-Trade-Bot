import os
import sys
import unittest
from datetime import datetime
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtester  # noqa: E402


def make_data(seed=7, n=700, end="2026-09-30"):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=end, periods=n)
    r = rng.normal(0.0006, 0.013, n)
    r[300:360] -= 0.012  # a drawdown so every regime appears
    qqq = 300 * np.exp(np.cumsum(r))
    lev = {"TQQQ": 3.0, "SQQQ": -3.0, "QLD": 2.0, "QID": -2.0}
    daily = {"QQQ": pd.DataFrame({"open": qqq * 0.999, "high": qqq * 1.01, "low": qqq * 0.99, "close": qqq, "volume": 1e6}, index=days)}
    for sym, k in lev.items():
        p = 50 * np.exp(np.cumsum(k * r))
        daily[sym] = pd.DataFrame({"open": p * 0.999, "high": p * 1.02, "low": p * 0.98, "close": p, "volume": 1e6}, index=days)
    intraday = {}
    for sym, frame in daily.items():
        rows = []
        for day, row in frame.iterrows():
            for minute in range(0, 6 * 60 + 30 + 60, 15):  # 9:30 .. 16:15 (one extended bar)
                t = day + pd.Timedelta(hours=9, minutes=30 + minute)
                frac = minute / 390
                px = row["open"] + (row["close"] - row["open"]) * min(frac, 1.0)
                rows.append((t, px, px))
        intraday[sym] = pd.DataFrame(rows, columns=["t", "open", "close"]).set_index("t")
    for frame in list(daily.values()) + list(intraday.values()):
        frame.attrs.update({"data_source": "alpaca", "feed": "sip", "data_quality": "OK", "adjustment": "split"})
    return daily, intraday


class AllocationBacktestTests(unittest.TestCase):
    def run_bt(self, daily, intraday, **kw):
        def fake(ticker, timeframe, start, end, force_yahoo=False, force_refresh=False):
            src = daily if timeframe == "1Day" else intraday
            frame = src[ticker]
            out = frame.loc[(frame.index >= pd.Timestamp(start).normalize()) & (frame.index <= pd.Timestamp(end))].copy()
            out.attrs.update(frame.attrs)
            return out
        args = dict(initial_capital=10000, allocation_mode="compound", trade_amount=10000, enable_risk=False,
                    strategy_preset="malik_qqq", strategy_preset_variant="long_inverse",
                    allocation_settings={"allocation_bull_etf": "TQQQ", "allocation_bear_etf": "SQQQ"})
        args.update(kw)
        ticker = args.pop("ticker", "QQQ")
        with mock.patch.object(backtester, "get_historical_data", side_effect=fake):
            bt = backtester.Backtester(ticker, args.pop("timeframe", "1Day"), datetime(2025, 9, 1), datetime(2026, 9, 30), **args)
            return bt.run()

    def test_runs_and_reports_the_allocation(self):
        daily, intraday = make_data()
        r = self.run_bt(daily, intraday)
        info = r["summary"]["strategy_preset"]
        self.assertEqual(info["kind"], "allocation")
        self.assertEqual(info["fill_model"], "intraday")
        self.assertEqual(info["legs"], {"bull": "TQQQ", "bear": "SQQQ"})
        self.assertGreater(info["fills"], 0)
        self.assertEqual(len(info["benchmarks"]), 3)
        self.assertEqual({v["id"] for v in info["variants"]}, {"long_only", "long_inverse"})
        self.assertIn("Did not pass", info["research_note"])
        self.assertTrue(all(t["fees"] == 0.0 for t in r["trades"]))
        # every decision fills at 15:45 ET on a normal day (bar after the decision bar)
        self.assertTrue(all(f["time"].endswith("15:45 ET") for f in info["rebalance_log"]))

    def test_no_lookahead_after_the_decision(self):
        daily, intraday = make_data()
        base = self.run_bt(daily, intraday)["summary"]["strategy_preset"]["rebalance_log"]
        # Corrupt every bar after the fill bar (16:00+) and the final day's daily close.
        for sym, frame in intraday.items():
            frame.loc[frame.index.time >= pd.Timestamp("16:00").time(), ["open", "close"]] *= 4
        for frame in daily.values():
            frame.iloc[-1, frame.columns.get_loc("close")] *= 4
        new = self.run_bt(daily, intraday)["summary"]["strategy_preset"]["rebalance_log"]
        self.assertEqual([(f["day"], f["symbol"], f["side"], f["qty"]) for f in base], [(f["day"], f["symbol"], f["side"], f["qty"]) for f in new])

    def test_rejects_wrong_ticker_and_timeframe(self):
        daily, intraday = make_data()
        self.assertIn("error", self.run_bt(daily, intraday, ticker="SPY"))
        self.assertIn("error", self.run_bt(daily, intraday, timeframe="15Min"))

    def test_max_position_cap_applies(self):
        daily, intraday = make_data()
        r = self.run_bt(daily, intraday, enable_risk=True, max_pos_pct=0.25, strategy_preset_variant="long_only")
        buys = [f for f in r["summary"]["strategy_preset"]["rebalance_log"] if f["side"] == "BUY"]
        self.assertTrue(buys)
        self.assertTrue(any(f["limit"] == "max_position" for f in buys))


if __name__ == "__main__":
    unittest.main()
