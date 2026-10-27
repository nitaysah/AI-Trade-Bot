import pandas as pd
import numpy as np
import math
import datetime
from data_manager import get_historical_data
from indicators import calculate_indicators_for_df, _generate_signals
from swing_strategy import build_entry_signal, build_swing_exit
from options_direction import validate_options_type
from options_pricing import black_scholes, calculate_historical_volatility, calculate_greeks
from option_selector import add_us_market_trading_days, trading_days_until, _strike_profile, _target_strike_from_profile
from indicator_catalog import EXTENSION_CHART_FIELDS, EXTENSION_CHART_GROUPS
from prior_day_high_low import buffered_prior_day_levels, resolve_high_low_buffer_pct
from trade_guards import is_stop_exit, opening_blackout_reason, quote_liquidity_reason, stabilization_reason

def _unix_timestamp(t):
    try:
        if hasattr(t, "tz") and t.tz is None:
            t = t.tz_localize("America/New_York")
        return int(t.timestamp())
    except Exception:
        return int(t.timestamp()) if hasattr(t, "timestamp") else int(t)

def _safe_chart_level(row, key, digits=4):
    try:
        value = row.get(key)
        if value is None or pd.isna(value):
            return None
        return round(float(value), digits)
    except Exception:
        return None

def _safe_chart_bool(row, key):
    try:
        value = row.get(key)
        if value is None or pd.isna(value):
            return False
        return bool(value)
    except Exception:
        return False

def _chart_indicator_fields(row, digits=4, enabled_indicators=None, buy_mode="", sell_mode=""):
    fields = {
        "ema_fast": _safe_chart_level(row, "EMA_Fast", digits),
        "ema_slow": _safe_chart_level(row, "EMA_Slow", digits),
        "sma": _safe_chart_level(row, "SMA", digits),
        "sma_secondary": _safe_chart_level(row, "SMA_Secondary", digits),
        "vwap": _safe_chart_level(row, "VWAP", digits),
        "supertrend": _safe_chart_level(row, "Supertrend", digits),
        "supertrend_up": _safe_chart_bool(row, "Supertrend_Trend"),
        "boll_upper": _safe_chart_level(row, "BOLL_Upper", digits),
        "boll_middle": _safe_chart_level(row, "BOLL_Middle", digits),
        "boll_lower": _safe_chart_level(row, "BOLL_Lower", digits),
        "daily_high_low_high": _safe_chart_level(row, "Daily_HighLow_High", digits),
        "daily_high_low_low": _safe_chart_level(row, "Daily_HighLow_Low", digits),
        "daily_high_low_high_trigger": _safe_chart_level(row, "Daily_HighLow_High_Trigger", digits),
        "daily_high_low_low_trigger": _safe_chart_level(row, "Daily_HighLow_Low_Trigger", digits),
        "daily_high_low_bullish": _safe_chart_bool(row, "Daily_HighLow_Bullish"),
        "daily_high_low_bearish": _safe_chart_bool(row, "Daily_HighLow_Bearish"),
        "pivot_p": _safe_chart_level(row, "Pivot_P", digits),
        "pivot_r1": _safe_chart_level(row, "Pivot_R1", digits),
        "pivot_s1": _safe_chart_level(row, "Pivot_S1", digits),
        "pivot_r2": _safe_chart_level(row, "Pivot_R2", digits),
        "pivot_s2": _safe_chart_level(row, "Pivot_S2", digits),
        "pivot_r3": _safe_chart_level(row, "Pivot_R3", digits),
        "pivot_s3": _safe_chart_level(row, "Pivot_S3", digits),
        "pivot_r4": _safe_chart_level(row, "Pivot_R4", digits),
        "pivot_s4": _safe_chart_level(row, "Pivot_S4", digits),
        "pivot_r5": _safe_chart_level(row, "Pivot_R5", digits),
        "pivot_s5": _safe_chart_level(row, "Pivot_S5", digits),
        "pivot_bullish": _safe_chart_bool(row, "Pivot_Bullish"),
        "pivot_bearish": _safe_chart_bool(row, "Pivot_Bearish"),
        "volume_profile_poc": _safe_chart_level(row, "Volume_Profile_POC", digits),
        "volume_profile_vah": _safe_chart_level(row, "Volume_Profile_VAH", digits),
        "volume_profile_val": _safe_chart_level(row, "Volume_Profile_VAL", digits),
        "volume_profile_bullish": _safe_chart_bool(row, "Volume_Profile_Bullish"),
        "volume_profile_bearish": _safe_chart_bool(row, "Volume_Profile_Bearish"),
        "swing_support_1": _safe_chart_level(row, "Swing_Support_1", digits),
        "swing_support_2": _safe_chart_level(row, "Swing_Support_2", digits),
        "swing_support_3": _safe_chart_level(row, "Swing_Support_3", digits),
        "swing_support_4": _safe_chart_level(row, "Swing_Support_4", digits),
        "swing_support_5": _safe_chart_level(row, "Swing_Support_5", digits),
        "swing_resistance_1": _safe_chart_level(row, "Swing_Resistance_1", digits),
        "swing_resistance_2": _safe_chart_level(row, "Swing_Resistance_2", digits),
        "swing_resistance_3": _safe_chart_level(row, "Swing_Resistance_3", digits),
        "swing_resistance_4": _safe_chart_level(row, "Swing_Resistance_4", digits),
        "swing_resistance_5": _safe_chart_level(row, "Swing_Resistance_5", digits),
        "swing_bullish": _safe_chart_bool(row, "Swing_Bullish"),
        "swing_bearish": _safe_chart_bool(row, "Swing_Bearish"),
        "rsi": _safe_chart_level(row, "RSI", 4),
        "macd_line": _safe_chart_level(row, "MACD_Line", 6),
        "macd_signal": _safe_chart_level(row, "MACD_Signal", 6),
        "macd_hist": _safe_chart_level(row, "MACD_Hist", 6),
        "adx": _safe_chart_level(row, "ADX", 4),
        "di_plus": _safe_chart_level(row, "DI_Plus", 4),
        "di_minus": _safe_chart_level(row, "DI_Minus", 4),
        "mystic_bull": _safe_chart_level(row, "Bull_Pulse", 4),
        "mystic_bear": _safe_chart_level(row, "Bear_Pulse", 4),
        "cdl_name": str(row.get("cdl_name", "Neutral")),
        "cdl_bullish": _safe_chart_bool(row, "cdl_bullish"),
        "cdl_bearish": _safe_chart_bool(row, "cdl_bearish"),
        "cp_name": str(row.get("CP_Name", "Neutral")),
        "cp_bullish": _safe_chart_bool(row, "CP_Bullish"),
        "cp_bearish": _safe_chart_bool(row, "CP_Bearish"),
        "cp_quality": _safe_chart_level(row, "CP_Quality", 0),
    }
    fields.update({
        api_key: _safe_chart_level(row, column, 6)
        for api_key, column in EXTENSION_CHART_FIELDS.items()
    })
    if enabled_indicators is None:
        return fields

    enabled = set(enabled_indicators)
    keys = set()
    groups = {
        "EMA Cross": {"ema_fast", "ema_slow"},
        "SMA": {"sma", "sma_secondary"},
        "VWAP": {"vwap"},
        "Auto-AVWAP": {"auto_avwap"},
        "Supertrend": {"supertrend", "supertrend_up"},
        "Bollinger": {"boll_upper", "boll_middle", "boll_lower"},
        "RSI": {"rsi"},
        "MACD": {"macd_line", "macd_signal", "macd_hist"},
        "ADX Trend": {"adx", "di_plus", "di_minus"},
        "Mystic Pulse": {"mystic_bull", "mystic_bear"},
        "Volume Profile": {"volume_profile_poc", "volume_profile_vah", "volume_profile_val", "volume_profile_bullish", "volume_profile_bearish"},
        "Candle Patterns": {"cdl_name", "cdl_bullish", "cdl_bearish"},
    }
    groups.update({
        indicator: set(field_names)
        for indicator, field_names in EXTENSION_CHART_GROUPS.items()
    })
    for indicator in enabled:
        keys.update(groups.get(indicator, set()))

    modes = {str(buy_mode or ""), str(sell_mode or "")}
    if "Prior Day High/Low" in enabled or modes & {"high_low", "hybrid_high_low"}:
        keys.update(key for key in fields if key.startswith("daily_high_low_"))
    if "Pivot Points (S/R)" in enabled or modes & {"pivot_points", "hybrid_pivot"}:
        keys.update(key for key in fields if key.startswith("pivot_"))
    if "Swing Points" in enabled or modes & {"swing", "hybrid_swing"}:
        keys.update(key for key in fields if key.startswith("swing_"))
    return {key: fields[key] for key in keys}

class OptionsBacktester:
    def __init__(
        self,
        ticker: str,
        timeframe: str,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        df=None,
        initial_capital: float = 1000.0,
        trade_amount: float = 100.0,
        expiry_days: int = 7,
        strike_type: str = "ATM",
        options_type: str = "calls_only",
        stop_loss_pct: float = 0.50,
        take_profit_pct: float = 1.00,
        exit_strategy: str = "hybrid",
        sell_mode: str = "indicator",
        close_before_expiry_hours: float = 2.0,
        threshold: int = 5,
        sell_threshold: int = 3,
        enabled_indicators: list = None,
        ext_hours: bool = True,
        r: float = 0.05,
        dividend_yield: float = 0.0,
        option_contract_fee: float = 0.65,
        cancel_event=None,
        intraday_only: bool = False,
        enable_signal_reset: bool = True,
        enable_smart_reentry: bool = False,
        smart_reentry_type: str = "RSI",
        smart_reentry_rsi_call_max: int = 70,
        smart_reentry_rsi_put_min: int = 30,
        smart_reentry_vwap_call_max_dist_pct: float = 0.5,
        smart_reentry_vwap_put_max_dist_pct: float = 0.5,
        enable_cooldown: bool = True,
        cooldown_minutes: int = None,
        buy_mode: str = "indicator",
        custom_buy_price: float = 0.0,
        custom_stop_loss_price: float = 0.0,
        custom_take_profit_price: float = 0.0,
        custom_underlying_stop_loss_price: float = 0.0,
        custom_underlying_take_profit_price: float = 0.0,
        atr_stop_multiplier: float = 2.0,
        atr_trail_multiplier: float = 3.0,
        atr_take_profit_multiplier: float = 4.0,
        enable_atr_trail: bool = True,
        regular_stop_loss_pct: float = 0.05,
        regular_take_profit_pct: float = 0.10,
        regular_trailing_pct: float = 0.03,
        enable_pct_trail: bool = True,
        risk_per_trade: float = 1.0,
        max_pos_pct: float = 1.0,
        entry_atr_multiplier: float = None,
        entry_pct_move: float = None,
        entry_atr_style: str = "pullback_reclaim",
        entry_pct_style: str = "dip_reclaim",
        swing_entry_mode: str = "bounce_retest",
        swing_entry_lookback_days: int = 20,
        swing_exit_lookback_days: int = 20,
        market_session: str = "Regular",
        enable_risk: bool = True,
        daily_high_low_buffer_pct: float = None,
        indicator_overrides: dict = None,
        post_stop_stabilization_bars: int = 3,
        max_consecutive_losses: int = 2,
        max_daily_loss_pct: float = 0.0,
        options_max_entry_spread_pct: float = 5.0,
        options_market_open_blackout_minutes: int = 15,
    ):
        self.ticker = ticker
        self.timeframe = timeframe
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.trade_amount = trade_amount
        self.expiry_days = expiry_days
        self.strike_type = strike_type
        self.options_type = validate_options_type(options_type)
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        # sell_mode is the unified exit strategy key (mirrors regular backtester).
        # exit_strategy is kept for backward-compat with old API callers.
        self.sell_mode = sell_mode if sell_mode != 'indicator' else exit_strategy
        self.exit_strategy = exit_strategy
        self.close_before_expiry_hours = close_before_expiry_hours
        self.threshold = int(threshold or 0)
        self.sell_threshold = int(sell_threshold or 0)
        self.enabled_indicators = enabled_indicators or []
        if self.enabled_indicators:
            self.threshold = max(1, self.threshold)
            self.sell_threshold = max(1, self.sell_threshold)
        self.ext_hours = ext_hours
        self.r = r
        self.cancel_event = cancel_event
        self.intraday_only = intraday_only
        self.enable_signal_reset = bool(enable_signal_reset)
        self.enable_smart_reentry = bool(enable_smart_reentry)
        self.daily_high_low_buffer_pct = resolve_high_low_buffer_pct(fallback=daily_high_low_buffer_pct)
        self.indicator_settings = {
            **(indicator_overrides if isinstance(indicator_overrides, dict) else {}),
            "DAILY_HIGH_LOW_BUFFER_PCT": self.daily_high_low_buffer_pct,
        }
        self.smart_reentry_type = str(smart_reentry_type or "RSI").upper()
        self.smart_reentry_rsi_call_max = float(smart_reentry_rsi_call_max or 70.0)
        self.smart_reentry_rsi_put_min = float(smart_reentry_rsi_put_min or 30.0)
        self.smart_reentry_vwap_call_max_dist_pct = float(smart_reentry_vwap_call_max_dist_pct or 0.5)
        self.smart_reentry_vwap_put_max_dist_pct = float(smart_reentry_vwap_put_max_dist_pct or 0.5)
        self.enable_cooldown = enable_cooldown
        self.cooldown_minutes = cooldown_minutes
        # Entry mode
        self.buy_mode = buy_mode
        self.custom_buy_price = custom_buy_price
        self.custom_stop_loss_price = custom_stop_loss_price
        self.custom_take_profit_price = custom_take_profit_price
        self.custom_underlying_stop_loss_price = custom_underlying_stop_loss_price
        self.custom_underlying_take_profit_price = custom_underlying_take_profit_price
        # ATR exit params
        self.atr_stop_multiplier = atr_stop_multiplier
        self.atr_trail_multiplier = atr_trail_multiplier
        self.atr_take_profit_multiplier = atr_take_profit_multiplier
        self.enable_atr_trail = bool(enable_atr_trail)
        # Percentage exit params
        self.regular_stop_loss_pct = regular_stop_loss_pct or stop_loss_pct
        self.regular_take_profit_pct = regular_take_profit_pct or take_profit_pct
        self.regular_trailing_pct = regular_trailing_pct
        self.enable_pct_trail = bool(enable_pct_trail)
        self.entry_atr_multiplier = entry_atr_multiplier or atr_stop_multiplier
        self.entry_pct_move = entry_pct_move or max(1.0, (regular_stop_loss_pct or stop_loss_pct) * 100.0)
        self.entry_atr_style = entry_atr_style or "pullback_reclaim"
        self.entry_pct_style = entry_pct_style or "dip_reclaim"
        self.swing_entry_mode = swing_entry_mode or "bounce_retest"
        self.swing_entry_lookback_days = swing_entry_lookback_days or 20
        self.swing_exit_lookback_days = swing_exit_lookback_days or 20
        self.indicator_settings.update({
            "SWING_POINTS_LOOKBACK_DAYS": self.swing_entry_lookback_days,
            "SWING_POINTS_ENTRY_MODE": self.swing_entry_mode,
            "market_session": market_session or "Regular",
        })
        self.market_session = market_session or "Regular"
        # Risk sizing
        self.risk_per_trade = risk_per_trade
        self.max_pos_pct = max_pos_pct
        self.enable_risk = enable_risk
        
        # Real-market pricing proxy parameters
        # Keep q explicit; a blanket SPY yield is incorrect for single stocks.
        self.q = max(0.0, float(dividend_yield or 0.0))
        self.option_contract_fee = max(0.0, float(option_contract_fee or 0.0))
        self.iv_markup = 0.04 # 4% markup for Implied Volatility over Historical
        self.spread_width = 0.03 # Typical $0.03 spread on liquid ETF options

        self.equity = initial_capital
        self.position = None  # None or Dict representing option position
        self.pending_order = None  # Signal generated at a bar close; filled at the next bar open.
        self.trades = []
        self.equity_history = []
        self.skipped_entries = []
        self.unfilled_signals = []
        self.signal_locked_direction = None
        self.last_exit_time = None
        self.pending_order = None
        self.post_stop_stabilization_bars = max(0, int(post_stop_stabilization_bars or 0))
        self.max_consecutive_losses = max(0, int(max_consecutive_losses or 0))
        self.max_daily_loss_pct = max(0.0, float(max_daily_loss_pct or 0.0))
        self.options_max_entry_spread_pct = min(100.0, max(0.0, float(options_max_entry_spread_pct or 0.0)))
        self.options_market_open_blackout_minutes = max(0, int(options_market_open_blackout_minutes or 0))
        self.stop_exit_state = None
        self.consecutive_losses = 0
        self.daily_realized_pl = 0.0
        self.guard_session_date = None
        self.raw_df = df
        self.df = None
        self.options_csv_cache = {}
        self.options_csv_by_timestamp = {}
        self.option_quote_capabilities = {"historical_rows": 0, "two_sided_rows": 0}
        self._load_options_csv_cache()
        self.data_source = "yahoo"

    def _load_options_csv_cache(self):
        """Load legacy historical option marks.

        The bundled file has one ``option_price`` column, rather than bid/ask
        quotes.  Treat it as a historical mark, never as an executable OBBO.
        A richer file can supply ``bid``/``ask`` (or ``bid_price``/``ask_price``)
        and will then receive true side-aware historical fills.  Newer files
        may include an ``iv`` column, which is used for the Greeks.
        """
        import os
        ticker_file = f"{self.ticker.lower()}_options_chain_5m.csv"
        for data_dir in ("data", os.path.join("backend", "data")):
            # Resolve the path and keep it inside the data folder, whatever the ticker text contains.
            base = os.path.abspath(data_dir)
            candidate = os.path.normpath(os.path.join(base, ticker_file))
            if not candidate.startswith(base + os.sep):
                continue
            if os.path.exists(candidate):
                try:
                    df_opt = pd.read_csv(candidate)
                    for _, row in df_opt.iterrows():
                        key = (str(row["timestamp"])[:16], round(float(row["strike_price"]), 1), str(row["option_type"]).upper(), int(row["expiration_dte"]))
                        price = pd.to_numeric(row.get("option_price"), errors="coerce")
                        iv = pd.to_numeric(row.get("iv"), errors="coerce")
                        bid = pd.to_numeric(row.get("bid", row.get("bid_price")), errors="coerce")
                        ask = pd.to_numeric(row.get("ask", row.get("ask_price")), errors="coerce")
                        if (pd.isna(price) or float(price) <= 0) and pd.notna(bid) and pd.notna(ask) and float(bid) > 0 and float(ask) >= float(bid):
                            price = (float(bid) + float(ask)) / 2.0
                        if pd.notna(price) and float(price) > 0:
                            expiration = row.get("expiration_date", row.get("expiration"))
                            if pd.notna(expiration):
                                try:
                                    expiration = pd.Timestamp(expiration).date().isoformat()
                                except (TypeError, ValueError):
                                    expiration = None
                            else:
                                expiration = None
                            contract_symbol = row.get("contract_symbol", row.get("option_symbol", row.get("symbol")))
                            record = {
                                "price": float(price),
                                "iv": float(iv) if pd.notna(iv) and float(iv) > 0 else None,
                                "bid": float(bid) if pd.notna(bid) and float(bid) > 0 else None,
                                "ask": float(ask) if pd.notna(ask) and float(ask) > 0 else None,
                                "expiration": expiration,
                                "contract_symbol": str(contract_symbol) if pd.notna(contract_symbol) else None,
                            }
                            # Legacy files identify a quote only by DTE.  Keep
                            # all records at a key so contract-expiry-aware data
                            # can never overwrite a different listed series.
                            self.options_csv_cache.setdefault(key, []).append(record)
                            lookup_key = (key[0], key[2], key[3])
                            self.options_csv_by_timestamp.setdefault(lookup_key, []).append((key[1], record))
                            self.option_quote_capabilities["historical_rows"] += 1
                            if record["bid"] and record["ask"] and record["ask"] >= record["bid"]:
                                self.option_quote_capabilities["two_sided_rows"] += 1
                    print(f"[options-csv] Loaded {len(self.options_csv_cache)} historical option marks from {candidate}")
                except Exception as e:
                    print(f"[options-csv] Warning loading CSV: {e}")
                break

    def _lookup_option_snapshot(self, timestamp, strike, option_type, expiration_dte, expiration_close=None, contract_symbol=None):
        """Return an exact quote for the selected contract, if one is available."""
        if not self.options_csv_cache:
            return None
        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        key = (ts_str[:16], round(float(strike), 1), str(option_type).upper(), int(expiration_dte))
        candidates = self.options_csv_cache.get(key, [])
        # Backward compatibility for callers/tests that construct one record.
        if isinstance(candidates, dict):
            candidates = [candidates]
        if not candidates:
            return None
        if contract_symbol:
            candidates = [quote for quote in candidates if quote.get("contract_symbol") == contract_symbol]
            if not candidates:
                return None
        if expiration_close is not None:
            expected_expiry = self._as_datetime(expiration_close).date().isoformat()
            exact = [quote for quote in candidates if quote.get("expiration") == expected_expiry]
            if exact:
                return exact[0]
            # Legacy files have no expiry field. They are accepted only because
            # the exact remaining DTE is already part of the lookup key.
            legacy = [quote for quote in candidates if not quote.get("expiration")]
            if legacy:
                return legacy[0]
            return None
        return candidates[0]

    def _nearest_historical_strike(self, timestamp, target_strike, option_type, expiration_dte, expiration_close):
        """Choose the nearest quoted strike for the selected expiry at entry.

        This keeps a historical run on an actually quoted contract when the
        theoretical target (for example a $0.50 increment) is not listed.
        """
        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        key = (ts_str[:16], str(option_type).upper(), int(expiration_dte))
        candidates = self.options_csv_by_timestamp.get(key, [])
        if not candidates:
            return None
        expected_expiry = self._as_datetime(expiration_close).date().isoformat()
        compatible = [
            (strike, quote)
            for strike, quote in candidates
            if not quote.get("expiration") or quote.get("expiration") == expected_expiry
        ]
        if not compatible:
            return None
        return min(compatible, key=lambda item: abs(float(item[0]) - float(target_strike)))[0]

    def _trading_dte(self, timestamp, expiration_close) -> int:
        """DTE for the *same contract* at a valuation timestamp."""
        ts = self._as_datetime(timestamp)
        expiry = self._as_datetime(expiration_close)
        if ts >= expiry:
            return 0
        return trading_days_until(ts.date(), expiry.date())

    def _resolve_option_valuation(
        self,
        timestamp,
        underlying_price: float,
        strike: float,
        option_type: str,
        expiration_close,
        historical_vol: float,
        side: str,
        contract_symbol: str = None,
    ):
        """Price a contract and return (executable_price, iv, price_source, symbol).

        A historical row is only eligible when its timestamp, strike, type and
        remaining DTE all match the held contract.  This prevents silently
        rolling a position into a new 7-DTE contract on the next trading day.
        """
        dte = self._trading_dte(timestamp, expiration_close)
        snapshot = self._lookup_option_snapshot(timestamp, strike, option_type, dte, expiration_close, contract_symbol)
        model_iv = max(0.01, float(historical_vol) + self.iv_markup)
        iv = float(snapshot["iv"]) if snapshot and snapshot.get("iv") else model_iv
        t_remaining = self._time_to_expiry_years(timestamp, expiration_close)

        if snapshot and snapshot.get("price", 0.0) > 0:
            bid = snapshot.get("bid")
            ask = snapshot.get("ask")
            if bid and ask and ask >= bid:
                executable = ask if str(side).upper() == "BUY" else bid
                return max(0.01, float(executable)), iv, "historical_obbo", snapshot.get("contract_symbol")
            # The legacy cache has a single mark, so applying a made-up spread
            # would produce an unjustified and asymmetric fill.
            return max(0.01, float(snapshot["price"])), iv, "historical_option_mark", snapshot.get("contract_symbol")

        theoretical = black_scholes(
            underlying_price, strike, t_remaining, self.r, iv, option_type, q=self.q
        )
        half_spread = self.spread_width / 2.0
        executable = theoretical + half_spread if str(side).upper() == "BUY" else theoretical - half_spread
        return max(0.01, executable), iv, "black_scholes_proxy", None

    def _calculate_historical_volatility_states(self, price_df: pd.DataFrame) -> pd.DataFrame:
        """Return realized volatility known at each bar's open and close.

        The opening state contains the prior close -> current open return but
        cannot use the current bar's close.  The close state then adds the
        intrabar return.  Separating the two prevents next-open fills from
        accidentally seeing a future close.
        """
        if price_df.empty:
            return pd.DataFrame(index=price_df.index, columns=["open", "close"], dtype=float)

        opens = pd.to_numeric(price_df["Open"], errors="coerce")
        closes = pd.to_numeric(price_df["Close"], errors="coerce")
        if getattr(price_df.index, "tz", None) is not None:
            sessions = list(price_df.index.tz_convert("America/New_York").date)
        else:
            sessions = list(price_df.index.date)

        samples = []
        open_sample_idx = []
        close_sample_idx = []
        previous_close = None
        previous_session = None
        for session, open_price, close_price in zip(sessions, opens, closes):
            valid_open = pd.notna(open_price) and float(open_price) > 0
            valid_close = pd.notna(close_price) and float(close_price) > 0
            if valid_open and previous_close is not None and previous_close > 0 and session != previous_session:
                samples.append(math.log(float(open_price) / previous_close))
            open_sample_idx.append(len(samples) - 1)

            if valid_open and valid_close:
                if session != previous_session or previous_close is None or previous_close <= 0:
                    samples.append(math.log(float(close_price) / float(open_price)))
                else:
                    samples.append(math.log(float(close_price) / previous_close))
                previous_close = float(close_price)
                previous_session = session
            close_sample_idx.append(len(samples) - 1)

        trading_sessions = max(1, len(set(sessions)))
        observations_per_day = max(1.0, len(samples) / trading_sessions)
        annualization = math.sqrt(252.0 * observations_per_day)
        sample_vol = pd.Series(samples, dtype=float).rolling(window=50, min_periods=20).std() * annualization

        def state_at(sample_indexes):
            values = []
            for sample_index in sample_indexes:
                value = sample_vol.iloc[sample_index] if sample_index >= 0 else np.nan
                values.append(float(value) if pd.notna(value) else 0.16)
            return pd.Series(np.clip(values, 0.05, 3.0), index=price_df.index, dtype=float)

        return pd.DataFrame({"open": state_at(open_sample_idx), "close": state_at(close_sample_idx)})

    def _calculate_historical_volatility(self, price_df: pd.DataFrame) -> pd.Series:
        """Backward-compatible close-of-bar realized-volatility accessor."""
        return self._calculate_historical_volatility_states(price_df)["close"]

    def _as_datetime(self, ts) -> datetime.datetime:
        if hasattr(ts, "to_pydatetime"):
            return ts.to_pydatetime()
        return ts

    def _expiration_close_for_entry(self, entry_time) -> datetime.datetime:
        entry_dt = self._as_datetime(entry_time)
        expiration_date = add_us_market_trading_days(entry_dt.date(), int(self.expiry_days or 0))
        close_time = datetime.time(16, 0)
        expiration_dt = datetime.datetime.combine(expiration_date, close_time)
        if entry_dt.tzinfo is not None:
            expiration_dt = expiration_dt.replace(tzinfo=entry_dt.tzinfo)
        return expiration_dt

    def _time_to_expiry_years(self, timestamp, expiration_close) -> float:
        ts = self._as_datetime(timestamp)
        remaining_seconds = (expiration_close - ts).total_seconds()
        return max(0.00001, remaining_seconds / (365.0 * 24.0 * 3600.0))

    def _hours_to_expiry(self, timestamp, expiration_close) -> float:
        ts = self._as_datetime(timestamp)
        return max(0.0, (expiration_close - ts).total_seconds() / 3600.0)

    def _localize_dt(self, ts):
        import pytz
        is_crypto = False
        clean_ticker = self.ticker.upper().replace("/", "")
        for base in ["USD", "USDT", "USDC"]:
            if clean_ticker.endswith(base):
                is_crypto = True
                break
        
        if is_crypto:
            tz = pytz.utc
        else:
            tz = pytz.timezone("America/New_York")
        
        localized_dt = tz.localize(ts)
        return localized_dt.astimezone(pytz.utc)

    def _to_utc_iso(self, ts):
        utc_dt = self._localize_dt(ts)
        return utc_dt.strftime("%Y-%m-%d %H:%M:%SZ")

    def _to_unix(self, ts):
        utc_dt = self._localize_dt(ts)
        return int(utc_dt.timestamp())

    def _to_central_str(self, ts):
        import pytz
        utc_dt = self._localize_dt(ts)
        central_tz = pytz.timezone("America/Chicago")
        central_dt = utc_dt.astimezone(central_tz)
        return central_dt.strftime("%Y-%m-%d %H:%M")

    def run(self) -> dict:
        # 1. Clean date formats
        if hasattr(self.start_date, "tzinfo") and self.start_date.tzinfo is not None:
            self.start_date = self.start_date.replace(tzinfo=None)
        if hasattr(self.end_date, "tzinfo") and self.end_date.tzinfo is not None:
            self.end_date = self.end_date.replace(tzinfo=None)

        if self.raw_df is not None:
            raw_df = self.raw_df
        else:
            # Fetch with padding where the provider can actually satisfy it.
            warmup_days = 100
            padded_start = self.start_date - datetime.timedelta(days=warmup_days)
            raw_df = get_historical_data(self.ticker, self.timeframe, padded_start, self.end_date, force_yahoo=False)

        if raw_df is None or raw_df.empty:
            return {"error": "No underlying historical data found."}

        # Normalize column names to Title Case to prevent KeyErrors
        raw_df = raw_df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume',
            'OPEN': 'Open', 'HIGH': 'High', 'LOW': 'Low', 'CLOSE': 'Close', 'VOLUME': 'Volume'
        })
        raw_df = raw_df.loc[:, ~raw_df.columns.duplicated()]

        # Filter RTH: Options are only traded during regular market hours. Enforce strictly for intraday.
        clean_ticker = self.ticker.upper().replace("/", "")
        is_crypto = any(clean_ticker.endswith(base) for base in ["USD", "USDT", "USDC"])
        if is_crypto:
            return {"error": "Options trading is not supported for cryptocurrency tickers."}
        if not is_crypto and self.timeframe != "1Day":
            try:
                time_index = pd.DatetimeIndex(raw_df.index)
                if time_index.tz is None:
                    time_index = time_index.tz_localize(
                        "America/New_York",
                        ambiguous="infer",
                        nonexistent="shift_forward",
                    )
                else:
                    time_index = time_index.tz_convert("America/New_York")
                time_idx = time_index.time
                start_time = pd.to_datetime("09:30:00").time()
                # Options on index ETFs (SPY, QQQ, DIA, IWM) trade until 4:15 PM ET. Others trade until 4:00 PM ET.
                end_str = "16:15:00" if clean_ticker in ["SPY", "QQQ", "DIA", "IWM"] else "16:00:00"
                end_time = pd.to_datetime(end_str).time()
                rth_mask = (time_idx >= start_time) & (time_idx <= end_time)
                raw_df = raw_df[rth_mask]
                print(f"[options-backtest] Strictly enforced Regular Trading Hours (RTH: 09:30 - {end_str[:5]}) for {clean_ticker}")
            except Exception as e:
                print(f"[options-backtest] RTH filtering failed: {e}")

        # 2. Add technical indicators (skip if already present)
        if "UT_Above" not in raw_df.columns:
            calc_settings = {
                **(self.indicator_settings or {}),
                "enabled_indicators": self.enabled_indicators,
                "buy_mode": self.buy_mode,
                "sell_mode": self.sell_mode,
                "_require_rsi_dependency": bool(
                    self.enable_smart_reentry
                    and str(self.smart_reentry_type or "RSI").upper() == "RSI"
                ),
            }
            res = calculate_indicators_for_df(raw_df, self.timeframe, self.ticker, settings=calc_settings)
            self.df = res["df"] if isinstance(res, dict) and "df" in res else res
        else:
            self.df = raw_df
            
        if self.df is None or (hasattr(self.df, 'empty') and self.df.empty):
            return {"error": "Failed to calculate technical indicators."}

        full_df = self.df
        
        # 3. Price next-open executions only with information available at the
        # open. Close-of-bar analytics additionally include that bar's return.
        vol_states = self._calculate_historical_volatility_states(full_df)
        full_df['Open_Historical_Vol'] = vol_states['open']
        full_df['Historical_Vol'] = vol_states['close']

        # 4. Map daily high/low/close for previous-day pivot and range exits
        if getattr(full_df.index, "tz", None) is not None:
            df_dates = pd.Series(full_df.index.tz_convert("America/Chicago").date, index=full_df.index)
        else:
            df_dates = pd.Series(full_df.index.date, index=full_df.index)
        daily_groups = full_df.groupby(df_dates)
        daily_highs = daily_groups['High'].max()
        daily_lows = daily_groups['Low'].min()
        daily_closes = daily_groups['Close'].last()

        unique_dates = sorted(list(daily_highs.index))
        prev_day_data = {}
        for idx, dt in enumerate(unique_dates):
            if idx == 0:
                prev_day_data[dt] = None
            else:
                prev_day_data[dt] = {
                    "High": daily_highs.iloc[idx-1],
                    "Low": daily_lows.iloc[idx-1],
                    "Close": daily_closes.iloc[idx-1]
                }

        # Slice back to window
        self.df = full_df.loc[self.start_date:self.end_date]
        if self.df.empty:
            return {"error": "No data available in requested range after filtering."}

        price_history_records = []
        for ts, row in self.df.iterrows():
            try:
                price_history_records.append({
                    "time": _unix_timestamp(ts),
                    "open": float(row.get("Open", row.get("open", row.get("Close", 0.0)))),
                    "high": float(row.get("High", row.get("high", row.get("Close", 0.0)))),
                    "low": float(row.get("Low", row.get("low", row.get("Close", 0.0)))),
                    "close": float(row.get("Close", row.get("close", 0.0))),
                    "volume": float(row.get("Volume", row.get("volume", 0.0))),
                })
            except Exception:
                continue

        # 5. Simulate bar-by-bar options trading
        self._today_high = None
        self._today_low = None
        self._last_date = None
        self.signal_locked_direction = None
        self.last_exit_time = None

        for i in range(len(self.df)):
            if self.cancel_event and self.cancel_event.is_set():
                print(f"[options-backtest] Cancelled by user halfway through execution ({i}/{len(self.df)} bars)")
                return {"error": "Backtest cancelled by user"}
                
            current_bar = self.df.iloc[i]
            timestamp = self.df.index[i]
            current_price = current_bar['Close']
            current_vol = current_bar['Historical_Vol']
            open_price = current_bar.get('Open', current_price)
            open_vol = current_bar.get('Open_Historical_Vol', current_vol)

            if i < 30:
                continue

            prev_bar = self.df.iloc[i-1]

            # Signals are created from a completed bar. Execute their market
            # order at the next tradable bar's open using only open-known IV.
            self._execute_pending_order(open_price, open_vol, timestamp)

            # Dynamic tracking of intraday high/low
            current_date = timestamp.tz_convert("America/Chicago").date() if getattr(timestamp, "tzinfo", None) is not None else timestamp.date()
            if self._last_date != current_date:
                self._today_high = current_bar['High']
                self._today_low = current_bar['Low']
                self._last_date = current_date
            else:
                self._today_high = max(self._today_high, current_bar['High'])
                self._today_low = min(self._today_low, current_bar['Low'])

            # Pre-fetch previous day boundaries
            p_data = prev_day_data.get(current_date)
            prev_h = p_data["High"] if p_data else None
            prev_l = p_data["Low"] if p_data else None
            prev_c = p_data["Close"] if p_data else None

            # Check exits if position is open
            if self.position:
                opt_type = self.position['option_type']
                strike = self.position['strike_price']
                entry_date = self.position['entry_time']

                # Compute current option premium with IV markup and dividend yield
                expiration_close = self.position.get('expiration_close') or self._expiration_close_for_entry(entry_date)
                opt_price, exit_iv, exit_price_source, exit_contract_symbol = self._resolve_option_valuation(
                    timestamp, current_price, strike, opt_type, expiration_close, current_vol, "SELL",
                    contract_symbol=self.position.get("contract_symbol"),
                )
                
                # Check exit triggers
                exit_triggered = False
                exit_reason = ""
                configured_exit_modes = {self.sell_mode or self.exit_strategy}
                
                # Intraday Check
                if self.intraday_only and timestamp.time() >= datetime.time(15, 55):
                    exit_triggered = True
                    exit_reason = "Intraday End of Day (15:55 PM ET)"

                # Expiration safeguard
                hours_remaining = self._hours_to_expiry(timestamp, expiration_close)
                if not exit_triggered and hours_remaining <= self.close_before_expiry_hours:
                    exit_triggered = True
                    exit_reason = f"Expiration Safeguard ({self.close_before_expiry_hours}h remaining)"

                # Strategy A: Custom premium SL/TP
                if not exit_triggered and configured_exit_modes.intersection({"custom_sltp"}):
                    if self.custom_stop_loss_price > 0 and opt_price <= self.custom_stop_loss_price:
                        exit_triggered = True
                        exit_reason = f"Custom Premium Stop Loss Hit (${opt_price:.2f} <= ${self.custom_stop_loss_price:.2f})"
                    elif self.custom_take_profit_price > 0 and opt_price >= self.custom_take_profit_price:
                        exit_triggered = True
                        exit_reason = f"Custom Premium Take Profit Hit (${opt_price:.2f} >= ${self.custom_take_profit_price:.2f})"

                # Strategy A2: Custom underlying-price SL/TP
                if not exit_triggered and configured_exit_modes.intersection({"custom_underlying_sltp"}):
                    if opt_type == "CALL":
                        if self.custom_underlying_stop_loss_price > 0 and current_price <= self.custom_underlying_stop_loss_price:
                            exit_triggered = True
                            exit_reason = f"Custom Underlying Stop Loss Hit (Underlying ${current_price:.2f} <= ${self.custom_underlying_stop_loss_price:.2f})"
                        elif self.custom_underlying_take_profit_price > 0 and current_price >= self.custom_underlying_take_profit_price:
                            exit_triggered = True
                            exit_reason = f"Custom Underlying Take Profit Hit (Underlying ${current_price:.2f} >= ${self.custom_underlying_take_profit_price:.2f})"
                    else:
                        if self.custom_underlying_stop_loss_price > 0 and current_price >= self.custom_underlying_stop_loss_price:
                            exit_triggered = True
                            exit_reason = f"Custom Underlying Stop Loss Hit (Underlying ${current_price:.2f} >= ${self.custom_underlying_stop_loss_price:.2f})"
                        elif self.custom_underlying_take_profit_price > 0 and current_price <= self.custom_underlying_take_profit_price:
                            exit_triggered = True
                            exit_reason = f"Custom Underlying Take Profit Hit (Underlying ${current_price:.2f} <= ${self.custom_underlying_take_profit_price:.2f})"

                # Strategy B: Dynamic Pivot Point Exit
                if not exit_triggered and prev_h is not None and prev_l is not None and prev_c is not None and configured_exit_modes.intersection({"pivot_points", "hybrid_pivot"}):
                    P = (prev_h + prev_l + prev_c) / 3.0
                    R1 = 2 * P - prev_l
                    S1 = 2 * P - prev_h

                    if opt_type == "CALL":
                        if current_bar['High'] >= R1:
                            exit_triggered = True
                            exit_reason = f"Pivot Take Profit Hit (Underlying High >= R1: {R1:.2f})"
                        elif current_bar['Low'] <= S1:
                            exit_triggered = True
                            exit_reason = f"Pivot Stop Loss Hit (Underlying Low <= S1: {S1:.2f})"
                    else: # PUT
                        if current_bar['Low'] <= S1:
                            exit_triggered = True
                            exit_reason = f"Pivot Take Profit Hit (Underlying Low <= S1: {S1:.2f})"
                        elif current_bar['High'] >= R1:
                            exit_triggered = True
                            exit_reason = f"Pivot Stop Loss Hit (Underlying High >= R1: {R1:.2f})"

                # Strategy C: Prior Day High/Low ranges
                if not exit_triggered and prev_h is not None and prev_l is not None and prev_c is not None and configured_exit_modes.intersection({"high_low", "hybrid_high_low"}):
                    hl_levels = buffered_prior_day_levels(
                        {"high": prev_h, "low": prev_l, "close": prev_c},
                        self.daily_high_low_buffer_pct,
                    )
                    if opt_type == "CALL" and hl_levels:
                        if current_bar['High'] >= hl_levels["high_trigger"]:
                            exit_triggered = True
                            exit_reason = f"Prev Day High Breakout (Underlying High >= Trigger {hl_levels['high_trigger']:.2f}, PDH {hl_levels['high']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                        elif current_bar['Low'] <= hl_levels["low_trigger"]:
                            exit_triggered = True
                            exit_reason = f"Prev Day Low Breakdown (Underlying Low <= Trigger {hl_levels['low_trigger']:.2f}, PDL {hl_levels['low']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                    elif opt_type == "PUT" and hl_levels:
                        if current_bar['Low'] <= hl_levels["low_trigger"]:
                            exit_triggered = True
                            exit_reason = f"Prev Day Low Breakout (Underlying Low <= Trigger {hl_levels['low_trigger']:.2f}, PDL {hl_levels['low']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"
                        elif current_bar['High'] >= hl_levels["high_trigger"]:
                            exit_triggered = True
                            exit_reason = f"Prev Day High Breakdown (Underlying High >= Trigger {hl_levels['high_trigger']:.2f}, PDH {hl_levels['high']:.2f}, Buffer {hl_levels['buffer_pct']:.2f}%)"

                # Strategy C2: Swing support/resistance exits
                if not exit_triggered and configured_exit_modes.intersection({"swing_points", "hybrid_swing"}):
                    swing_exit = build_swing_exit(
                        price_history_records[:i + 1],
                        current_price=current_price,
                        is_long=(opt_type == "CALL"),
                        lookback_days=int(self.swing_exit_lookback_days or 20),
                        market_session=self.market_session,
                        is_crypto=False,
                    )
                    tp = swing_exit.get("take_profit")
                    sl = swing_exit.get("stop_loss")
                    if tp is not None and sl is not None:
                        if opt_type == "CALL":
                            if current_price >= tp:
                                exit_triggered = True
                                exit_reason = f"Swing Take Profit Hit (Underlying {current_price:.2f} >= {tp:.2f})"
                            elif current_price <= sl:
                                exit_triggered = True
                                exit_reason = f"Swing Stop Loss Hit (Underlying {current_price:.2f} <= {sl:.2f})"
                        else:
                            if current_price <= tp:
                                exit_triggered = True
                                exit_reason = f"Swing Take Profit Hit (Underlying {current_price:.2f} <= {tp:.2f})"
                            elif current_price >= sl:
                                exit_triggered = True
                                exit_reason = f"Swing Stop Loss Hit (Underlying {current_price:.2f} >= {sl:.2f})"

                # Strategy D: Indicator Reversal (Indicator/Hybrid modes)
                if not exit_triggered and configured_exit_modes.intersection({"indicator", "hybrid", "hybrid_pct", "hybrid_pivot", "hybrid_high_low", "hybrid_swing"}):
                    signals = _generate_signals(
                        current_bar,
                        prev_bar,
                        settings={
                            **(self.indicator_settings or {}),
                            "enabled_indicators": self.enabled_indicators,
                        },
                        recent_bars=self.df.iloc[max(0, i - 4) : i + 1],
                    )
                    filtered_signals = {k: v for k, v in signals.items() if k in self.enabled_indicators}
                    bearish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BEARISH')
                    bullish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BULLISH')
                    
                    if opt_type == "CALL" and bearish_count >= self.sell_threshold:
                        exit_triggered = True
                        exit_reason = f"Indicator Reversal Signal ({bearish_count} Bearish)"
                    elif opt_type == "PUT" and bullish_count >= self.sell_threshold:
                        exit_triggered = True
                        exit_reason = f"Indicator Reversal Signal ({bullish_count} Bullish)"

                if exit_triggered:
                    self._queue_exit(exit_reason, timestamp)
                    continue

                # ── Resolve effective exit strategy ─────────────────────────
                # sell_mode maps the UI dropdown to a strategy key.
                # If sell_mode is set (non-indicator), it overrides exit_strategy.
                eff_mode = self.sell_mode or self.exit_strategy

                # ── ATR-based Stop-Loss / Take-Profit ───────────────────────
                current_atr = current_bar.get('ATR', None)
                if current_atr is None or current_atr == 0:
                    current_atr = current_price * 0.01  # 1% fallback

                if not exit_triggered and eff_mode in ['sltp', 'hybrid', 'atr']:
                    if opt_type == 'CALL':
                        atr_sl = self.position['entry_underlying'] - self.atr_stop_multiplier * current_atr
                        if self.enable_atr_trail and getattr(self, 'atr_trail_multiplier', 0) > 0:
                            trail_sl = current_price - self.atr_trail_multiplier * current_atr
                            cur_trail = self.position.get('trailing_sl', atr_sl)
                            if trail_sl > cur_trail:
                                self.position['trailing_sl'] = trail_sl
                            atr_sl = max(atr_sl, self.position.get('trailing_sl', atr_sl))
                        atr_tp = self.position['entry_underlying'] + self.atr_take_profit_multiplier * current_atr
                        if current_price <= atr_sl:
                            exit_triggered = True
                            self.position['initial_stop_price'] = self.position.get('initial_stop_price') or atr_sl
                            self.position['exit_stop_price'] = atr_sl
                            exit_reason = f"ATR Stop Loss Hit (Underlying {current_price:.2f} <= SL {atr_sl:.2f})"
                        elif current_price >= atr_tp:
                            exit_triggered = True
                            exit_reason = f"ATR Take Profit Hit (Underlying {current_price:.2f} >= TP {atr_tp:.2f})"
                    else:  # PUT profits when underlying falls
                        atr_sl = self.position['entry_underlying'] + self.atr_stop_multiplier * current_atr
                        if self.enable_atr_trail and getattr(self, 'atr_trail_multiplier', 0) > 0:
                            trail_sl = current_price + self.atr_trail_multiplier * current_atr
                            cur_trail = self.position.get('trailing_sl', atr_sl)
                            if trail_sl < cur_trail:
                                self.position['trailing_sl'] = trail_sl
                            atr_sl = min(atr_sl, self.position.get('trailing_sl', atr_sl))
                        atr_tp = self.position['entry_underlying'] - self.atr_take_profit_multiplier * current_atr
                        if current_price >= atr_sl:
                            exit_triggered = True
                            self.position['initial_stop_price'] = self.position.get('initial_stop_price') or atr_sl
                            self.position['exit_stop_price'] = atr_sl
                            exit_reason = f"ATR Stop Loss Hit (Underlying {current_price:.2f} >= SL {atr_sl:.2f})"
                        elif current_price <= atr_tp:
                            exit_triggered = True
                            exit_reason = f"ATR Take Profit Hit (Underlying {current_price:.2f} <= TP {atr_tp:.2f})"

                # ── Percentage Stop-Loss / Take-Profit on Premium ───────────
                if not exit_triggered and eff_mode in ['percentage', 'hybrid_pct', 'premium_pct']:
                    pct_sl = self.regular_stop_loss_pct
                    pct_tp = self.regular_take_profit_pct
                    trail_pct = self.regular_trailing_pct
                    entry_prem = self.position['entry_premium']
                    # Track trailing high-water mark
                    if 'trail_high' not in self.position:
                        self.position['trail_high'] = opt_price
                    else:
                        self.position['trail_high'] = max(self.position['trail_high'], opt_price)
                    trail_sl = self.position['trail_high'] * (1 - trail_pct)
                    if opt_price <= entry_prem * (1 - pct_sl):
                        exit_triggered = True
                        self.position['initial_stop_price'] = self.position.get('initial_stop_price') or (entry_prem * (1 - pct_sl))
                        self.position['exit_stop_price'] = entry_prem * (1 - pct_sl)
                        exit_reason = f"Premium Stop Loss Hit (-{pct_sl*100:.0f}%)"
                    elif opt_price >= entry_prem * (1 + pct_tp):
                        exit_triggered = True
                        exit_reason = f"Premium Take Profit Hit (+{pct_tp*100:.0f}%)"
                    elif self.enable_pct_trail and trail_pct > 0 and opt_price < trail_sl and self.position['trail_high'] > entry_prem:
                        exit_triggered = True
                        self.position['initial_stop_price'] = self.position.get('initial_stop_price') or (entry_prem * (1 - pct_sl))
                        self.position['exit_stop_price'] = trail_sl
                        exit_reason = f"Trailing Stop Hit (Premium fell {trail_pct*100:.0f}% from high)"

                if exit_triggered:
                    self._queue_exit(exit_reason, timestamp)
                    continue

            # Evaluate entries if no position is held
            else:
                signals = _generate_signals(
                    current_bar,
                    prev_bar,
                    settings={
                        **(self.indicator_settings or {}),
                        "enabled_indicators": self.enabled_indicators,
                    },
                    recent_bars=self.df.iloc[max(0, i - 4) : i + 1],
                )
                filtered_signals = {k: v for k, v in signals.items() if k in self.enabled_indicators}
                
                bullish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BULLISH')
                bearish_count = sum(s.get('weight', 1) for s in filtered_signals.values() if s['signal'] == 'BEARISH')
                buy_threshold = max(1, int(self.threshold or 1))
                sell_threshold = max(1, int(self.sell_threshold or buy_threshold))
                bullish_ready = bullish_count >= buy_threshold
                bearish_ready = bearish_count >= sell_threshold
                bullish_dominates = bullish_ready and (not bearish_ready or bullish_count > bearish_count)
                bearish_dominates = bearish_ready and (not bullish_ready or bearish_count > bullish_count)

                desired_direction = "BULLISH" if self.options_type == "calls_only" else "BEARISH"
                option_type = "CALL" if self.options_type == "calls_only" else "PUT"
                expected_action = "BUY" if desired_direction == "BULLISH" else "SELL"
                indicator_action = "HOLD"
                if filtered_signals:
                    if desired_direction == "BULLISH" and bullish_dominates:
                        indicator_action = "BUY"
                    elif desired_direction == "BEARISH" and bearish_dominates:
                        indicator_action = "SELL"

                entry_mode = self.buy_mode
                price_action_modes = ['custom_price', 'atr', 'hybrid_atr', 'percentage', 'hybrid_pct', 'pivot_points', 'hybrid_pivot', 'high_low', 'hybrid_high_low', 'swing_points', 'hybrid_swing']
                hybrid_modes = {'hybrid_atr', 'hybrid_pct', 'hybrid_pivot', 'hybrid_high_low', 'hybrid_swing'}
                entry_action = "HOLD"
                entry_reason = ""
                if entry_mode in price_action_modes:
                    entry_signal = build_entry_signal(
                        price_history_records[: i + 1],
                        current_price=current_price,
                        entry_mode=entry_mode,
                        lookback_days=self.swing_entry_lookback_days,
                        market_session=self.market_session,
                        is_crypto=False,
                        atr=current_bar.get('ATR', current_price * 0.01),
                        custom_buy_price=self.custom_buy_price,
                        entry_atr_mult=self.entry_atr_multiplier,
                        entry_pct_move=self.entry_pct_move,
                        entry_atr_style=self.entry_atr_style,
                        entry_pct_style=self.entry_pct_style,
                        swing_entry_mode=self.swing_entry_mode,
                        desired_direction=desired_direction,
                        high_low_buffer_pct=self.daily_high_low_buffer_pct,
                    )
                    structure_action = entry_signal.get("action", "HOLD")
                    structure_ok = structure_action == expected_action and entry_signal.get("structure_direction") == desired_direction
                    indicator_ok = indicator_action == expected_action

                    if entry_mode in hybrid_modes:
                        if indicator_ok and structure_ok:
                            entry_action = expected_action
                            entry_reason = f"Hybrid {option_type}: {bullish_count if option_type=='CALL' else bearish_count} indicator signals + {entry_signal.get('reason', 'structure confirmed')}"
                    elif structure_ok:
                        entry_action = expected_action
                        entry_reason = entry_signal.get("reason", f"Entry trigger: {entry_mode}")

                elif indicator_action == expected_action:
                    entry_action = expected_action
                    entry_reason = (
                        f"Bullish signals crossover ({bullish_count} Bullish)"
                        if option_type == "CALL"
                        else f"Bearish signals crossover ({bearish_count} Bearish)"
                    )

                if self.enable_signal_reset and self.signal_locked_direction:
                    locked_dir = self.signal_locked_direction
                    reset_count = bullish_count if locked_dir == "BUY" else bearish_count
                    if entry_action != locked_dir and reset_count <= 0:
                        self.signal_locked_direction = None

                allowed, block_reason = self._entry_guard_allows(entry_action, current_bar, current_price, timestamp)
                if entry_action == expected_action and allowed:
                    self._queue_entry(
                        option_type=option_type,
                        reason=entry_reason or f"{option_type} entry triggered",
                        timestamp=timestamp,
                        underlying_price=current_price,
                    )

            # Record equity curve
            self.equity_history.append({
                'time': self._to_utc_iso(timestamp),
                'equity': round(self.equity + (self._get_unrealized_pl(current_price, current_vol, timestamp) if self.position else 0.0), 2)
            })

        # Force close open option at the very end
        if self.position:
            last_bar = self.df.iloc[-1]
            last_ts = self.df.index[-1]
            pending_exit = self.pending_order if self.pending_order and self.pending_order.get("kind") == "EXIT" else None
            expiration_close = self.position.get('expiration_close') or self._expiration_close_for_entry(self.position['entry_time'])
            opt_price, exit_iv, exit_price_source, exit_contract_symbol = self._resolve_option_valuation(
                last_ts,
                last_bar['Close'],
                self.position['strike_price'],
                self.position['option_type'],
                expiration_close,
                last_bar['Historical_Vol'],
                "SELL",
                contract_symbol=self.position.get("contract_symbol"),
            )
            self._exit_option_trade(
                opt_price,
                last_bar['Close'],
                last_ts,
                pending_exit.get("reason") if pending_exit else "End of Backtest",
                vol=last_bar['Historical_Vol'], iv=exit_iv, price_source=exit_price_source,
                contract_symbol=exit_contract_symbol,
                signal_time=pending_exit.get("signal_time") if pending_exit else last_ts,
                fill_timing="end_of_backtest_close",
            )
            self.pending_order = None
        elif self.pending_order and self.pending_order.get("kind") == "ENTRY":
            self.unfilled_signals.append({
                "time": self._to_utc_iso(self.pending_order.get("signal_time")),
                "reason": "Signal not filled: no subsequent tradable bar in the requested backtest window.",
                "option_type": self.pending_order.get("option_type"),
            })
            self.pending_order = None

        return self._calculate_metrics()

    def _cooldown_block_reason(self, timestamp):
        if not self.enable_cooldown or self.last_exit_time is None:
            return None
        from engine import parse_timeframe_to_seconds
        tf_seconds = parse_timeframe_to_seconds(self.timeframe)
        cooldown_secs = self.cooldown_minutes * 60 if self.cooldown_minutes is not None else tf_seconds * 4
        if (timestamp - self.last_exit_time).total_seconds() < cooldown_secs:
            return "Trade Cooldown Active"
        return None

    def _refresh_guard_session(self, timestamp):
        session_date = timestamp.date()
        if self.guard_session_date != session_date:
            self.guard_session_date = session_date
            self.daily_realized_pl = 0.0
            self.consecutive_losses = 0

    def _loss_circuit_reason(self, timestamp):
        self._refresh_guard_session(timestamp)
        if self.max_consecutive_losses > 0 and self.consecutive_losses >= self.max_consecutive_losses:
            return f"Loss circuit breaker active ({self.consecutive_losses} consecutive losses)"
        loss_limit = self.initial_capital * self.max_daily_loss_pct
        if loss_limit > 0 and self.daily_realized_pl <= -loss_limit:
            return f"Daily strategy-loss limit active (${abs(self.daily_realized_pl):.2f} loss)"
        return None

    def _smart_reentry_allows(self, action, bar, current_price):
        if action not in ["BUY", "SELL"] or not self.enable_smart_reentry:
            return True, None

        if self.smart_reentry_type == "RSI":
            try:
                current_rsi = float(bar.get("RSI"))
            except (TypeError, ValueError):
                return True, None
            if action == "BUY" and current_rsi > self.smart_reentry_rsi_call_max:
                return False, f"Smart Re-entry RSI Filter: RSI {current_rsi:.1f} > max {self.smart_reentry_rsi_call_max:.1f}"
            if action == "SELL" and current_rsi < self.smart_reentry_rsi_put_min:
                return False, f"Smart Re-entry RSI Filter: RSI {current_rsi:.1f} < min {self.smart_reentry_rsi_put_min:.1f}"
            return True, None

        if self.smart_reentry_type == "VWAP":
            try:
                current_vwap = float(bar.get("VWAP"))
                current_price = float(current_price)
            except (TypeError, ValueError):
                return True, None
            if current_vwap <= 0 or current_price <= 0:
                return True, None
            dist_pct = abs(current_price - current_vwap) / current_vwap * 100.0
            if action == "BUY" and dist_pct > self.smart_reentry_vwap_call_max_dist_pct:
                return False, f"Smart Re-entry VWAP Filter: distance {dist_pct:.2f}% > max {self.smart_reentry_vwap_call_max_dist_pct:.2f}%"
            if action == "SELL" and dist_pct > self.smart_reentry_vwap_put_max_dist_pct:
                return False, f"Smart Re-entry VWAP Filter: distance {dist_pct:.2f}% > max {self.smart_reentry_vwap_put_max_dist_pct:.2f}%"
        return True, None

    def _entry_guard_allows(self, action, bar, current_price, timestamp):
        if action not in ["BUY", "SELL"]:
            return True, None

        if self.enable_signal_reset and self.signal_locked_direction == action:
            return False, f"Signal Reset Active: waiting for stale {action} signal to reset"

        cooldown_reason = self._cooldown_block_reason(timestamp)
        if cooldown_reason:
            return False, cooldown_reason

        circuit_reason = self._loss_circuit_reason(timestamp)
        if circuit_reason:
            return False, circuit_reason

        opening_reason = opening_blackout_reason(
            timestamp,
            self.options_market_open_blackout_minutes,
            enabled=True,
        )
        if opening_reason:
            return False, opening_reason

        if self.stop_exit_state:
            from engine import parse_timeframe_to_seconds
            elapsed_seconds = max(0.0, (timestamp - self.stop_exit_state["time"]).total_seconds())
            elapsed_bars = int(elapsed_seconds // max(1, parse_timeframe_to_seconds(self.timeframe)))
            stabilization = stabilization_reason(
                action,
                bar,
                self.stop_exit_state,
                elapsed_bars,
                self.post_stop_stabilization_bars,
            )
            if stabilization:
                return False, stabilization
            self.stop_exit_state = None

        return self._smart_reentry_allows(action, bar, current_price)

    def _queue_entry(self, option_type: str, reason: str, timestamp, underlying_price: float):
        if self.position is None and self.pending_order is None:
            strike_mode = str(self.strike_type or "ATM").upper()
            profile = _strike_profile(strike_mode)
            selected_strike = _target_strike_from_profile(
                underlying_price,
                option_type,
                strike_mode,
                profile.get("target_depth_pct", 0.0),
            )
            selected_strike = round(selected_strike * 2) / 2
            expiration_close = self._expiration_close_for_entry(timestamp)
            historical_strike = self._nearest_historical_strike(
                timestamp,
                selected_strike,
                option_type,
                self._trading_dte(timestamp, expiration_close),
                expiration_close,
            )
            self.pending_order = {
                "kind": "ENTRY",
                "option_type": option_type,
                "reason": reason,
                "signal_time": timestamp,
                "strike_price": float(historical_strike) if historical_strike is not None else selected_strike,
                "expiration_close": expiration_close,
            }

    def _queue_exit(self, reason: str, timestamp):
        # Keep the first exit signal; it is the condition that was actionable
        # earliest and will be sent at the next bar open.
        if self.position is not None and self.pending_order is None:
            self.pending_order = {
                "kind": "EXIT",
                "reason": reason,
                "signal_time": timestamp,
            }

    def _execute_pending_order(self, underlying_open: float, open_vol: float, timestamp):
        order = self.pending_order
        if not order:
            return False
        self.pending_order = None

        if order["kind"] == "ENTRY":
            if self.position is None:
                self._enter_option_trade(
                    option_type=order["option_type"],
                    underlying_price=float(underlying_open),
                    vol=float(open_vol),
                    timestamp=timestamp,
                    reason=order["reason"],
                    signal_time=order["signal_time"],
                    fill_timing="next_bar_open",
                    strike_override=order.get("strike_price"),
                    expiration_close_override=order.get("expiration_close"),
                )
                return self.position is not None
            return False

        if order["kind"] == "EXIT" and self.position is not None:
            expiration_close = self.position.get("expiration_close") or self._expiration_close_for_entry(self.position["entry_time"])
            option_premium, exit_iv, price_source, contract_symbol = self._resolve_option_valuation(
                timestamp,
                float(underlying_open),
                self.position["strike_price"],
                self.position["option_type"],
                expiration_close,
                float(open_vol),
                "SELL",
                contract_symbol=self.position.get("contract_symbol"),
            )
            self._exit_option_trade(
                option_premium,
                float(underlying_open),
                timestamp,
                order["reason"],
                vol=float(open_vol),
                iv=exit_iv,
                price_source=price_source,
                contract_symbol=contract_symbol,
                signal_time=order["signal_time"],
                fill_timing="next_bar_open",
            )
            return True
        return False

    def _enter_option_trade(
        self,
        option_type: str,
        underlying_price: float,
        vol: float,
        timestamp: datetime.datetime,
        reason: str,
        signal_time=None,
        fill_timing: str = "bar_close",
        strike_override: float = None,
        expiration_close_override=None,
    ):
        # Determine Strike Price
        if strike_override is None:
            strike_mode = str(self.strike_type or "ATM").upper()
            profile = _strike_profile(strike_mode)
            strike = _target_strike_from_profile(
                underlying_price,
                option_type,
                strike_mode,
                profile.get("target_depth_pct", 0.0),
            )
            strike = round(strike * 2) / 2
        else:
            strike = float(strike_override)
        
        # Price the exact selected contract at entry.  A cached historical mark
        # is used only when it matches this timestamp/strike/type/DTE; otherwise
        # Black-Scholes-Merton plus the configured synthetic spread is used.
        expiration_close = expiration_close_override or self._expiration_close_for_entry(timestamp)
        if strike_override is None:
            historical_strike = self._nearest_historical_strike(
                timestamp,
                strike,
                option_type,
                self._trading_dte(timestamp, expiration_close),
                expiration_close,
            )
            if historical_strike is not None:
                strike = float(historical_strike)
        t_years = self._time_to_expiry_years(timestamp, expiration_close)
        entry_premium, entry_iv, entry_price_source, entry_contract_symbol = self._resolve_option_valuation(
            timestamp, underlying_price, strike, option_type, expiration_close, vol, "BUY"
        )

        snapshot = self._lookup_option_snapshot(
            timestamp,
            strike,
            option_type,
            self._trading_dte(timestamp, expiration_close),
            expiration_close,
            entry_contract_symbol,
        )
        if snapshot and snapshot.get("bid") and snapshot.get("ask"):
            liquidity_reason, _, _ = quote_liquidity_reason(
                bid=snapshot.get("bid"),
                ask=snapshot.get("ask"),
                max_spread_pct=self.options_max_entry_spread_pct,
            )
            if liquidity_reason:
                if len(self.skipped_entries) < 25:
                    self.skipped_entries.append({
                        "time": self._to_utc_iso(timestamp),
                        "reason": liquidity_reason,
                        "option_type": option_type,
                    })
                return
        elif entry_price_source == "black_scholes_proxy":
            modeled_mid = max(0.01, entry_premium - (self.spread_width / 2.0))
            modeled_spread_pct = (self.spread_width / modeled_mid) * 100.0
            if modeled_spread_pct > self.options_max_entry_spread_pct:
                if len(self.skipped_entries) < 25:
                    self.skipped_entries.append({
                        "time": self._to_utc_iso(timestamp),
                        "reason": f"Liquidity filter: modeled spread {modeled_spread_pct:.2f}% exceeds {self.options_max_entry_spread_pct:.2f}%",
                        "option_type": option_type,
                    })
                return

        if entry_premium <= 0:
            return

        contract_multiplier = 100.0
        single_contract_cost = entry_premium * contract_multiplier + self.option_contract_fee

        # Risk-based sizing
        risk_amount = self.equity * self.risk_per_trade
        risk_per_contract = single_contract_cost
        max_notional = self.equity * self.max_pos_pct

        if self.enable_risk:
            # Estimate risk distance per contract
            if self.sell_mode in ['percentage', 'hybrid_pct']:
                risk_per_contract = single_contract_cost * self.regular_stop_loss_pct
            else:
                # Conservative fallback: assume total loss of premium
                risk_per_contract = single_contract_cost

            if risk_per_contract <= 0:
                risk_per_contract = single_contract_cost

            risk_based_qty = risk_amount / risk_per_contract
            notional_qty = max_notional / single_contract_cost
        else:
            risk_based_qty = self.equity / single_contract_cost
            notional_qty = self.equity / single_contract_cost
        
        # Trade amount budget cap (Allocation limit)
        budget_qty = self.trade_amount / single_contract_cost
        
        # Final quantity is the minimum of all constraints (and can't exceed cash on hand)
        qty = math.floor(min(risk_based_qty, notional_qty, budget_qty, self.equity / single_contract_cost))
        if qty <= 0 and self.equity >= single_contract_cost:
            qty = 1

        if qty <= 0:
            limiting_reasons = []
            if self.enable_risk and risk_based_qty < 1:
                limiting_reasons.append(
                    f"risk cap ${risk_amount:.2f} is below estimated risk ${risk_per_contract:.2f}"
                )
            if budget_qty < 1:
                limiting_reasons.append(
                    f"allocation ${self.trade_amount:.2f} is below contract cost ${single_contract_cost:.2f}"
                )
            if self.enable_risk and notional_qty < 1:
                limiting_reasons.append(
                    f"max position cap ${max_notional:.2f} is below contract cost ${single_contract_cost:.2f}"
                )
            if (self.equity / single_contract_cost) < 1:
                limiting_reasons.append(
                    f"equity ${self.equity:.2f} is below contract cost ${single_contract_cost:.2f}"
                )
            limit_text = "; ".join(limiting_reasons) or "sizing rules allow less than 1 contract"
            if len(self.skipped_entries) < 25:
                self.skipped_entries.append({
                    "time": self._to_utc_iso(timestamp),
                    "reason": (
                        f"Skipped {option_type}: {limit_text}. Estimated premium ${entry_premium:.2f} "
                        f"(contract cost ${single_contract_cost:.2f})."
                    ),
                    "option_type": option_type,
                    "estimated_premium": round(float(entry_premium), 4),
                    "estimated_contract_cost": round(float(single_contract_cost), 2),
                    "trade_amount": round(float(self.trade_amount), 2),
                    "underlying_price": round(float(underlying_price), 2),
                    "strike": round(float(strike), 2),
                })
            return

        total_cost = qty * single_contract_cost
        entry_greeks = calculate_greeks(underlying_price, strike, t_years, self.r, entry_iv, option_type, q=self.q)
        # Greeks are BSM values at the valuation IV; price reflects the actual
        # historical mark or modeled execution price used for the fill.
        entry_greeks["price"] = round(float(entry_premium), 4)

        # Record position
        self.position = {
            "option_type": option_type,
            "strike_price": strike,
            "entry_time": timestamp,
            "entry_signal_time": signal_time or timestamp,
            "entry_fill_timing": fill_timing,
            "expiration_date": expiration_close.date(),
            "expiration_close": expiration_close,
            "entry_underlying": underlying_price,
            "entry_premium": entry_premium,
            "entry_price": entry_premium,
            "entry_iv": entry_iv,
            "entry_greeks": entry_greeks,
            "entry_price_source": entry_price_source,
            "contract_symbol": entry_contract_symbol,
            "qty": qty,
            "contract_cost": single_contract_cost,
            "total_cost": total_cost,
            "entry_fees": qty * self.option_contract_fee,
            "initial_stop_price": None,
            "exit_stop_price": None,
            "reason": reason
        }

    def _exit_option_trade(
        self,
        option_premium: float,
        underlying_price: float,
        timestamp: datetime.datetime,
        reason: str,
        vol: float = None,
        iv: float = None,
        price_source: str = None,
        contract_symbol: str = None,
        signal_time=None,
        fill_timing: str = "bar_close",
    ):
        contract_multiplier = 100.0
        exit_fees = self.position['qty'] * self.option_contract_fee
        exit_value = self.position['qty'] * option_premium * contract_multiplier - exit_fees
        pl = exit_value - self.position['total_cost']
        pl_pct = (pl / self.position['total_cost']) * 100.0

        # Equity is tracked as account value, so realize only P/L on close.
        self.equity += pl

        exit_stop_price = self.position.get('exit_stop_price')
        initial_stop_price = self.position.get('initial_stop_price')
        stop_note = ""
        if exit_stop_price is not None:
            if initial_stop_price is not None and abs(float(exit_stop_price) - float(initial_stop_price)) > 1e-9:
                stop_note = f" | Stop Used: ${float(exit_stop_price):.4f} (initial ${float(initial_stop_price):.4f})"
            else:
                stop_note = f" | Stop Used: ${float(exit_stop_price):.4f}"

        # Revalue exit Greeks using the exit bar's IV, not the entry IV.  This
        # is essential across close-to-open moves where realized volatility can
        # change materially before the next session opens.
        expiration_close = self.position.get('expiration_close') or self._expiration_close_for_entry(self.position['entry_time'])
        t_remaining = self._time_to_expiry_years(timestamp, expiration_close)
        exit_iv = float(iv) if iv is not None else max(0.01, float(vol if vol is not None else 0.16) + self.iv_markup)
        exit_greeks = calculate_greeks(underlying_price, self.position['strike_price'], t_remaining, self.r, exit_iv, self.position['option_type'], q=self.q)
        exit_greeks['price'] = round(float(option_premium), 4)

        self.trades.append({
            'ticker': self.ticker,
            'entry_time': self._to_utc_iso(self.position['entry_time']),
            'exit_time': self._to_utc_iso(timestamp),
            'entry_signal_time': self._to_utc_iso(self.position.get('entry_signal_time', self.position['entry_time'])),
            'exit_signal_time': self._to_utc_iso(signal_time or timestamp),
            'entry_fill_timing': self.position.get('entry_fill_timing', 'bar_close'),
            'exit_fill_timing': fill_timing,
            'qty': int(self.position['qty']),
            'option_type': self.position['option_type'],
            'expiration_date': self.position.get('expiration_date').isoformat() if self.position.get('expiration_date') else None,
            'strike': self.position['strike_price'],
            'entry_price': round(self.position['entry_premium'], 4),
            'exit_price': round(option_premium, 4),
            'entry_underlying': round(self.position['entry_underlying'], 2),
            'exit_underlying': round(underlying_price, 2),
            'entry_cost': round(self.position['total_cost'], 2),
            'exit_value': round(exit_value, 2),
            'fees': round(self.position.get('entry_fees', 0.0) + exit_fees, 2),
            'pl': round(pl, 2),
            'pl_pct': round(pl_pct, 2),
            'reason': reason,
            'initial_stop_price': round(initial_stop_price, 4) if initial_stop_price is not None else None,
            'exit_stop_price': round(exit_stop_price, 4) if exit_stop_price is not None else None,
            'entry_greeks': self.position.get('entry_greeks', {}),
            'exit_greeks': exit_greeks,
            'entry_iv': round(self.position.get('entry_iv', 0.20) * 100.0, 2),
            'exit_iv': round(exit_iv * 100.0, 2),
            'entry_price_source': self.position.get('entry_price_source', 'black_scholes_proxy'),
            'exit_price_source': price_source or 'black_scholes_proxy',
            'contract_symbol': contract_symbol or self.position.get('contract_symbol'),
        })
        print(f"[{self._to_central_str(timestamp)}] SELL {self.ticker} {self.position['option_type']} @ ${option_premium:.4f} | P/L: ${pl:.2f} ({pl_pct:+.2f}%) | Reason: {reason}{stop_note}")
        if self.enable_signal_reset:
            self.signal_locked_direction = 'BUY' if self.position['option_type'] == 'CALL' else 'SELL'
        self._refresh_guard_session(timestamp)
        self.daily_realized_pl += float(pl)
        self.consecutive_losses = self.consecutive_losses + 1 if pl < 0 else 0
        if is_stop_exit(reason):
            self.stop_exit_state = {
                "direction": "BUY" if self.position['option_type'] == 'CALL' else "SELL",
                "exit_price": float(underlying_price),
                "time": timestamp,
            }
        self.last_exit_time = timestamp
        self.position = None

    def _get_unrealized_pl(self, current_price: float, vol: float, timestamp: datetime.datetime) -> float:
        opt_type = self.position['option_type']
        strike = self.position['strike_price']
        entry_date = self.position['entry_time']
        expiration_close = self.position.get('expiration_close') or self._expiration_close_for_entry(entry_date)
        opt_price, _, _, _ = self._resolve_option_valuation(
            timestamp, current_price, strike, opt_type, expiration_close, vol, "SELL",
            contract_symbol=self.position.get("contract_symbol"),
        )
        
        contract_multiplier = 100.0
        current_value = self.position['qty'] * opt_price * contract_multiplier - (self.position['qty'] * self.option_contract_fee)
        return current_value - self.position['total_cost']

    def _calculate_metrics(self) -> dict:
        if not self.trades:
            start_price = float(self.df.iloc[0].get('Open', self.df.iloc[0]['Close'])) if self.df is not None and not self.df.empty else 0.0
            end_price = float(self.df.iloc[-1]['Close']) if self.df is not None and not self.df.empty else start_price
            buy_hold_equity = (end_price / start_price) * self.initial_capital if start_price > 0 else self.initial_capital
            buy_hold_roi = ((end_price - start_price) / start_price) * 100.0 if start_price > 0 else 0.0
            total_bars = len(self.df) if self.df is not None else 0
            candles = []
            if self.df is not None:
                for i in range(total_bars):
                    row = self.df.iloc[i]
                    ts = self.df.index[i]
                    try:
                        candles.append({
                            "time": self._to_unix(ts),
                            "open": round(float(row.get('Open', row.get('open', 0))), 4),
                            "high": round(float(row.get('High', row.get('high', 0))), 4),
                            "low": round(float(row.get('Low', row.get('low', 0))), 4),
                            "close": round(float(row.get('Close', row.get('close', 0))), 4),
                            "volume": int(row.get('Volume', row.get('volume', 0))),
                            **_chart_indicator_fields(row, 4, self.enabled_indicators, self.buy_mode, self.sell_mode),
                        })
                    except Exception:
                        continue
            no_trade_reason = (
                self.skipped_entries[0]["reason"]
                if self.skipped_entries
                else "No option entries confirmed during the period. Try lowering thresholds, enabling more indicators, or choosing a looser entry strategy."
            )
            return {
                "summary": {
                    "ticker": self.ticker,
                    "timeframe": self.timeframe,
                    "period_days": max(0, (self.end_date - self.start_date).days),
                    "start_date_utc": self.start_date.isoformat(),
                    "end_date_utc": self.end_date.isoformat(),
                    "data_source": self.data_source,
                    "initial_capital": self.initial_capital,
                    "final_equity": round(self.equity, 2),
                    "total_pl": 0.0,
                    "roi_pct": 0.0,
                    "buy_hold_equity": round(buy_hold_equity, 2),
                    "buy_hold_roi_pct": round(buy_hold_roi, 2),
                    "win_rate_pct": 0.0,
                    "total_trades": 0,
                    "profitable_trades": 0,
                    "losing_trades": 0,
                    "avg_trade_pl": 0.0,
                    "best_trade": 0.0,
                    "worst_trade": 0.0,
                    "max_drawdown_pct": 0.0,
                    "sharpe_ratio": 0.0,
                    "sortino_ratio": 0.0,
                    "calmar_ratio": 0.0,
                    "profit_factor": 0.0,
                    "win_loss_ratio": 0.0,
                    "avg_hold_hours": 0.0,
                    "expectancy": 0.0,
                    "max_win_streak": 0,
                    "max_loss_streak": 0,
                    "no_trade_reason": no_trade_reason,
                    "skipped_entry_count": len(self.skipped_entries),
                    "unfilled_signal_count": len(self.unfilled_signals),
                    "avg_greeks": {},
                    "option_pricing_model": "Black-Scholes-Merton (continuous dividend yield)",
                    "option_contract_fee": self.option_contract_fee,
                    "volatility_method": "50-return realized volatility with separate open/close states",
                    "execution_model": "Signals at bar close; market fills at next tradable bar open",
                    "option_price_sources": [],
                    "historical_quote_capabilities": self.option_quote_capabilities,
                },
                "equity_curve": self.equity_history[::max(1, len(self.equity_history)//200)] if self.equity_history else [],
                "trades": [],
                "candles": candles,
                "diagnostics": {
                    "no_trade_reason": no_trade_reason,
                    "skipped_entries": self.skipped_entries,
                },
            }

        df_trades = pd.DataFrame(self.trades)
        win_rate = (df_trades['pl'] > 0).mean() * 100.0
        total_pl = self.equity - self.initial_capital
        roi = (total_pl / self.initial_capital) * 100.0

        max_dd = 0.0
        if self.equity_history:
            equity_vals = [e['equity'] for e in self.equity_history]
            peak = equity_vals[0]
            for v in equity_vals:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd

        pl_series = df_trades['pl'].values
        entry_costs = df_trades['entry_cost'].values
        returns = np.where(entry_costs > 0, pl_series / entry_costs, 0.0)

        winning_pl = df_trades[df_trades['pl'] > 0]['pl']
        losing_pl = df_trades[df_trades['pl'] <= 0]['pl'].abs()
        gross_profit = winning_pl.sum()
        gross_loss = losing_pl.sum()
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (None if gross_profit > 0 else 0.0)
        avg_win = round(winning_pl.mean(), 2) if len(winning_pl) > 0 else 0.0
        avg_loss = round(losing_pl.mean(), 2) if len(losing_pl) > 0 else 0.0
        win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (None if avg_win > 0 else 0.0)
        avg_win_pct = round(df_trades[df_trades['pl'] > 0]['pl_pct'].mean(), 2) if len(winning_pl) > 0 else 0.0
        avg_loss_pct = round(df_trades[df_trades['pl'] <= 0]['pl_pct'].mean(), 2) if len(losing_pl) > 0 else 0.0
        wr = win_rate / 100.0
        expectancy = round((wr * avg_win) - ((1 - wr) * avg_loss), 2)

        sharpe = 0.0
        if len(returns) > 1 and returns.std() > 0:
            sharpe = round((returns.mean() / returns.std()) * np.sqrt(252), 2)

        sortino = 0.0
        downside = returns[returns < 0]
        if len(downside) > 1 and downside.std() > 0:
            sortino = round((returns.mean() / downside.std()) * np.sqrt(252), 2)

        calmar = 0.0
        period_days = max((self.end_date - self.start_date).days, 1)
        annualised_roi = roi * (365 / period_days)
        if max_dd > 0:
            calmar = round(annualised_roi / max_dd, 2)

        avg_hold_hours = 0.0
        try:
            entry_times = pd.to_datetime(df_trades['entry_time'])
            exit_times = pd.to_datetime(df_trades['exit_time'])
            hold_durations = (exit_times - entry_times).dt.total_seconds() / 3600
            avg_hold_hours = round(hold_durations.mean(), 1)
        except Exception:
            pass

        max_win_streak = max_loss_streak = cur_win = cur_loss = 0
        for pl_val in df_trades['pl']:
            if pl_val > 0:
                cur_win += 1
                cur_loss = 0
            else:
                cur_loss += 1
                cur_win = 0
            max_win_streak = max(max_win_streak, cur_win)
            max_loss_streak = max(max_loss_streak, cur_loss)

        # Calculate Buy & Hold from the first available bar (Open to final Close)
        start_price = float(self.df.iloc[0].get('Open', self.df.iloc[0]['Close']))
        end_price = float(self.df.iloc[-1]['Close'])
        buy_hold_equity = (end_price / start_price) * self.initial_capital
        buy_hold_roi = ((end_price - start_price) / start_price) * 100.0

        total_bars = len(self.df)
        candles = []
        for i in range(total_bars):
            row = self.df.iloc[i]
            ts = self.df.index[i]
            try:
                unix_time = self._to_unix(ts)
                candles.append({
                    "time": unix_time,
                    "open": round(float(row.get('Open', row.get('open', 0))), 4),
                    "high": round(float(row.get('High', row.get('high', 0))), 4),
                    "low": round(float(row.get('Low', row.get('low', 0))), 4),
                    "close": round(float(row.get('Close', row.get('close', 0))), 4),
                    "volume": int(row.get('Volume', row.get('volume', 0))),
                    **_chart_indicator_fields(row, 4, self.enabled_indicators, self.buy_mode, self.sell_mode),
                })
            except Exception:
                continue

        # Calculate average Greeks across all option executions
        avg_greeks = {}
        if self.trades:
            for k in ["delta", "gamma", "theta", "vega", "rho", "iv"]:
                vals = [t["entry_greeks"].get(k, 0.0) for t in self.trades if isinstance(t.get("entry_greeks"), dict) and k in t["entry_greeks"]]
                avg_greeks[k] = round(float(np.mean(vals)), 4) if vals else 0.0
        price_sources = sorted({
            source
            for trade in self.trades
            for source in (trade.get("entry_price_source"), trade.get("exit_price_source"))
            if source
        })

        return {
            "summary": {
                "ticker": self.ticker,
                "timeframe": self.timeframe,
                "period_days": max(0, (self.end_date - self.start_date).days),
                "start_date_utc": self.start_date.isoformat(),
                "end_date_utc": self.end_date.isoformat(),
                "data_source": self.data_source,
                "options_type": self.options_type,
                "expiry_days": self.expiry_days,
                "strike_type": self.strike_type,
                "buy_mode": self.buy_mode,
                "sell_mode": self.sell_mode,
                "enable_risk": self.enable_risk,
                "initial_capital": self.initial_capital,
                "final_equity": round(self.equity, 2),
                "total_pl": round(total_pl, 2),
                "roi_pct": round(roi, 2),
                "buy_hold_equity": round(buy_hold_equity, 2),
                "buy_hold_roi_pct": round(buy_hold_roi, 2),
                "win_rate_pct": round(win_rate, 2),
                "total_trades": len(self.trades),
                "profitable_trades": int(len(df_trades[df_trades['pl'] > 0])),
                "losing_trades": int(len(df_trades[df_trades['pl'] <= 0])),
                "avg_trade_pl": round(df_trades['pl'].mean(), 2),
                "best_trade": round(df_trades['pl'].max(), 2),
                "worst_trade": round(df_trades['pl'].min(), 2),
                "max_drawdown_pct": round(max_dd, 2),
                "sharpe_ratio": sharpe,
                "sortino_ratio": sortino,
                "calmar_ratio": calmar,
                "profit_factor": profit_factor,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "avg_win_pct": avg_win_pct,
                "avg_loss_pct": avg_loss_pct,
                "win_loss_ratio": win_loss_ratio,
                "avg_hold_hours": avg_hold_hours,
                "expectancy": expectancy,
                "max_win_streak": max_win_streak,
                "max_loss_streak": max_loss_streak,
                "skipped_entry_count": len(self.skipped_entries),
                "unfilled_signal_count": len(self.unfilled_signals),
                "avg_greeks": avg_greeks,
                "option_pricing_model": "Black-Scholes-Merton (continuous dividend yield)",
                "option_contract_fee": self.option_contract_fee,
                "volatility_method": "50-return realized volatility with separate open/close states",
                "execution_model": "Signals at bar close; market fills at next tradable bar open",
                "option_price_sources": price_sources,
                "historical_quote_capabilities": self.option_quote_capabilities,
            },
            "equity_curve": self.equity_history[::max(1, len(self.equity_history)//200)],
            "trades": self.trades[::-1],
            "candles": candles,
        }
