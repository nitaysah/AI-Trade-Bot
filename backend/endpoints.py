
@app.post("/api/alpaca_config")
async def update_alpaca_config(cfg: AlpacaConfig, user: dict = Depends(verify_token)):
    success = eng.broker.connect(cfg.api_key, cfg.secret_key, cfg.paper)
    eng = user_manager.get_engine(user['uid'])
    if success:
        # Persist to cloud (Encrypted)
        await save_config_to_cloud(cfg.api_key, cfg.secret_key, cfg.paper)
        
        # Update config module state
        eng.config.ALPACA_API_KEY = cfg.api_key
        eng.config.ALPACA_SECRET_KEY = cfg.secret_key
        eng.config.ALPACA_PAPER = cfg.paper
        
        return {"status": "success", "message": "Connected to Alpaca."}
    else:
        return {"status": "error", "message": "Failed to connect to Alpaca. Check your keys."}


@app.delete("/api/alpaca_config")
def unlink_alpaca(, user: dict = Depends(verify_token)):
    # Delete from cloud
    eng = user_manager.get_engine(user['uid'])
    if db:
        db.collection("settings").document("alpaca").delete()
    
    # Reset broker to simulation
    eng.broker.simulation_mode = True
    eng.broker.client = None
    
    # Clear config module state
    eng.config.ALPACA_API_KEY = ""
    eng.config.ALPACA_SECRET_KEY = ""
    return {"status": "success", "message": "Alpaca account unlinked. Switched to simulation mode."}
    
@app.get("/api/dashboard")
async def get_dashboard(ticker: str = None, timeframe: str = None, mode: str = "heavy", user: dict = Depends(verify_token)):
    """
    Main dashboard endpoint — returns everything the UI needs in one call.
    """
    eng = user_manager.get_engine(user['uid'])
    overall_start = time.perf_counter()
    started = time.perf_counter()
    mode = (mode or "heavy").lower()
    if timeframe is None:
        timeframe = eng.config.DEFAULT_TIMEFRAME
        
    account = eng.broker.get_account_info()
    positions = eng.broker.get_positions()
    risk_mgr = get_risk_manager()

    # Determine which ticker to focus on for the chart/analysis
    # Priority: 1. URL Param, 2. First Active Bot, 3. First Watchlist Item, 4. TSLA fallback
    global dashboard_primary_ticker
    primary_ticker = ticker.upper() if ticker else (
        eng.config.TRADELIST[0] if eng.config.TRADELIST else (
            eng.config.WATCHLIST[0] if eng.config.WATCHLIST else "TSLA"
        )
    )
    dashboard_primary_ticker = primary_ticker  # Tell the background loop what user is viewing
    
    # ─── FRESH ANALYSIS ───
    # If it's an active bot, we prioritize the background scan to avoid conflicts
    is_active_bot = primary_ticker in eng.config.TRADELIST
    primary_scan = eng._pick_scan(primary_ticker, timeframe, prefer_bot=is_active_bot)
    if mode != "fast" and not primary_scan:
        # Use settled cash (non-marginable) for crypto
        is_crypto = any(c in primary_ticker for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in primary_ticker
        avail_cash = account.get('non_marginable_buying_power', account['cash']) if is_crypto else account['cash']

        primary_scan = await asyncio.to_thread(
            evaluate_trade,
            primary_ticker, 
            account_equity=account['equity'], 
            available_cash=avail_cash,
            timeframe=timeframe,
            data_source="webull"  # Dashboard always uses Webull for higher fidelity research
        )
        if primary_scan:
            _record_scan(primary_scan)
            if is_active_bot:
                _record_bot_scan(primary_scan)
    
    if not primary_scan:
        primary_scan = eng._pick_scan(primary_ticker, timeframe, prefer_bot=is_active_bot) or {}
    sentiment_score = primary_scan.get('sentiment_score', 0)
    sentiment_confidence = primary_scan.get('sentiment_confidence', 0)

    if sentiment_score > 0.3:
        sentiment_label = "Bullish"
    elif sentiment_score < -0.3:
        sentiment_label = "Bearish"
    else:
        sentiment_label = "Neutral"

    # Format daily P/L (Today Only)
    daily_pl = account.get('daily_pl', 0)
    daily_pl_pct = account.get('daily_pl_pct', 0)
    daily_pl_sign = "+" if daily_pl >= 0 else ""

    # Calculate All-Time Profit (Realized + Unrealized)
    # 1. Sum all realized P/L from history
    total_realized_pl = 0 # Can be enhanced by Alpaca Account Activities API later
    # 2. Sum all unrealized P/L from current positions
    total_unrealized_pl = sum(p.get('unrealized_pl', 0) for p in positions)
    
    total_profit = total_realized_pl + total_unrealized_pl
    
    # Estimate total profit % based on current equity
    # If equity is 100k and profit is 10k, then initial was 90k, so 10/90 = 11%
    initial_est = account['equity'] - total_profit
    total_profit_pct = (total_profit / initial_est * 100) if initial_est > 0 else 0
    total_pl_sign = "+" if total_profit >= 0 else ""

    payload = {
        # Portfolio Summary Cards
        "capital": f"${account['equity']:.2f}",
        "cash": f"${account['cash']:.2f}",
        "openPositions": str(len(positions)),
        "positionsList": ", ".join(p['symbol'] for p in positions) if positions else "No positions",
        "dailyPL": f"{daily_pl_sign}${daily_pl:.2f} ({daily_pl_sign}{daily_pl_pct:.1f}%)",
        "totalProfit": f"{total_pl_sign}${total_profit:.2f} ({total_pl_sign}{total_profit_pct:.1f}%)",
        "aiSentiment": f"{sentiment_label} ({sentiment_score})",
        "sentiment_confidence": sentiment_confidence,
        "sentiment_summary": primary_scan.get("sentiment_summary", ""),
        "sentiment_key_factor": primary_scan.get("sentiment_key_factor", "N/A"),
        "tickerAmounts": eng.config.TICKER_AMOUNTS,
        "ticker_settings": eng.config.TICKER_SETTINGS,
        "simulation": account.get('simulation', True),
        "has_keys": bool(eng.config.ALPACA_API_KEY),

        # Detailed Data
        "positions": positions,
        "recentTrades": [_format_trade_for_ui(t) for t in eng.trade_log],
        "orderHistory": eng.broker.get_order_history(),
        "pendingOrders": eng.broker.get_open_orders(),
        "watchlistScans": {
            ticker: _format_scan_for_ui(
                eng._pick_scan(ticker, timeframe, prefer_bot=False) or 
                eng._pick_scan(ticker, getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {}).get('timeframe', eng.config.DEFAULT_TIMEFRAME), prefer_bot=False, ignore_freshness=True) or {}
            )
            for ticker in eng.config.WATCHLIST
        },
        "botScans": {
            ticker: _format_scan_for_ui(
                eng._pick_scan(ticker, timeframe, prefer_bot=True) or 
                eng._pick_scan(ticker, getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {}).get('timeframe', eng.config.DEFAULT_TIMEFRAME), prefer_bot=True, ignore_freshness=True) or {}
            )
            for ticker in eng.config.TRADELIST
        },

        # Strategy Signals (primary ticker)
        "primaryTicker": primary_ticker,
        "signals": _format_scan_for_ui(primary_scan).get('signals', {}),
        "priceHistory": primary_scan.get('price_history', []),

        # Risk Management
        "risk": risk_mgr.get_risk_status(account['equity']),
        "ticker_settings": getattr(config, 'TICKER_SETTINGS', {}),

        # Bot Meta
        "botRunning": eng.bot_running,
        "lastScan": last_scan_time or "Starting...",
        "indicator_settings": {k: getattr(config, k, True) for k in dir(config) if k.startswith("ENABLE_")},
        "indicator_parameters": {k: getattr(config, k) for k in dir(config) if k.isupper() and not k.startswith("_") and k not in ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "GROQ_API_KEY", "FERNET_KEY"]},
        "strategyTimeframe": timeframe,
        "watchlist": eng.config.WATCHLIST,
        "tradelist": eng.config.TRADELIST,

        # Performance Timings
        "performance": {
            "total_ms": (time.perf_counter() - overall_start) * 1000,
            "eval_ms": primary_scan.get('perf_ms', 0),
            "cached": primary_scan.get('cached', False)
        },
        "scanInterval": eng.config.SCAN_INTERVAL_SECONDS,

        # Safety Controls
        "maxOpenPositions": getattr(config, 'MAX_OPEN_POSITIONS', 5),
        "tradeCooldownSeconds": getattr(config, 'TRADE_COOLDOWN_SECONDS', 300),
        "marketHoursOnly": getattr(config, 'MARKET_HOURS_ONLY', True),

        "debug_logs": cloud_restore_log
    }
    if mode == "fast":
        payload["signals"] = {}
        payload["priceHistory"] = []
        payload["watchlistScans"] = {
            k: {
                "ticker": v.get("ticker", ""),
                "price": v.get("price", ""),
                "action": v.get("action", "HOLD"),
                "reason": v.get("reason", ""),
                "bullish_count": v.get("bullish_count", 0),
                "bearish_count": v.get("bearish_count", 0),
                "total_signals": v.get("total_signals", 0),
                "signals": {}
            } for k, v in payload["watchlistScans"].items()
        }
        payload["botScans"] = {
            k: {
                "ticker": v.get("ticker", ""),
                "price": v.get("price", ""),
                "action": v.get("action", "HOLD"),
                "reason": v.get("reason", ""),
                "bullish_count": v.get("bullish_count", 0),
                "bearish_count": v.get("bearish_count", 0),
                "total_signals": v.get("total_signals", 0),
                "signals": {}
            } for k, v in payload["botScans"].items()
        }
        # asyncio.create_task(_warm_timeframe_scans(timeframe, primary_ticker=primary_ticker, limit=5))
    print(f"[perf] /api/dashboard mode={mode} {primary_ticker} {timeframe}: {(time.perf_counter() - started) * 1000:.1f}ms")
    return payload


@app.get("/api/scan/{ticker}")
def scan_ticker(ticker: str, timeframe: str = "4Hour", user: dict = Depends(verify_token)):
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker.upper()):
    eng = user_manager.get_engine(user['uid'])
        return {"error": "Invalid ticker format"}
    """On-demand scan of a specific ticker."""
    account = eng.broker.get_account_info()
    if account.get('simulation', True):
        return {"error": "Alpaca connection required for scanning."}
    # ─── CACHING ───
    now = datetime.now()
    cached_scan = eng._pick_scan(ticker.upper(), timeframe, prefer_bot=False)
    is_recent = False
    if cached_scan and cached_scan.get('timeframe') == timeframe:
        try:
            last_time = datetime.fromisoformat(cached_scan.get('time', ''))
            if (now - last_time).total_seconds() < 30:
                is_recent = True
        except: pass

    if is_recent:
        return _format_scan_for_ui(cached_scan)

    # Use settled cash (non-marginable) for crypto
    is_crypto = any(c in ticker.upper() for c in ["BTC", "ETH", "LTC", "SOL", "DOGE"]) or "USD" in ticker.upper()
    avail_cash = account.get('non_marginable_buying_power') or account['cash'] if is_crypto else account['cash']

    result = evaluate_trade(
        ticker.upper(), 
        account_equity=account['equity'], 
        available_cash=avail_cash,
        timeframe=timeframe
    )
    if result:
        _record_scan(result)
        return _format_scan_for_ui(result)
    return {"error": f"Could not analyze {ticker}"}


@app.post("/api/backtest")
async def run_backtest(data: dict, user: dict = Depends(verify_token)):
    """
    eng = user_manager.get_engine(user['uid'])
    Runs a historical backtest for a ticker.
    """
    ticker = data.get("ticker", "AAPL").upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        return {"status": "error", "message": "Invalid ticker format"}
    timeframe = data.get("timeframe", "1Day")
    days = int(data.get("days", 30))
    capital = float(data.get("capital", 1000.0))
    threshold = int(data.get("threshold", 5))
    sell_threshold = int(data.get("sell_threshold", 3))
    indicators = data.get("indicators", []) # List of names like ['RSI', 'MACD']
    ext_hours = data.get("ext_hours", True)
    
    end_date = get_now()
    start_date = end_date - timedelta(days=days)
    
    sell_mode = data.get("sell_mode", "indicator")
    risk_per_trade = float(data.get("risk_per_trade", 0.02))
    max_pos_pct = float(data.get("max_position_pct", 0.25))
    atr_stop_multiplier = float(data.get("atr_stop_multiplier", 2.0))
    atr_trail_multiplier = float(data.get("atr_trail_multiplier", 3.0))
    atr_take_profit_multiplier = float(data.get("atr_take_profit_multiplier", 4.0))
    
    # Backtester uses get_historical_data internally
    bt = Backtester(
        ticker=ticker,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        threshold=threshold,
        sell_threshold=sell_threshold,
        enabled_indicators=indicators,
        risk_per_trade=risk_per_trade,
        max_pos_pct=max_pos_pct,
        ext_hours=ext_hours,
        sell_mode=sell_mode,
        atr_stop_multiplier=atr_stop_multiplier,
        atr_trail_multiplier=atr_trail_multiplier,
        atr_take_profit_multiplier=atr_take_profit_multiplier
    )
    
    try:
        results = bt.run()
        if "error" in results:
            return {"status": "error", "message": results["error"]}
        return {"status": "success", "results": results}
    except Exception as e:
        print(f"[api] Backtest crash: {e}")
        return {"status": "error", "message": "An internal error occurred during backtest simulation."}


@app.post("/api/download_all")
async def download_all_data(data: dict, user: dict = Depends(verify_token)):
    """
    eng = user_manager.get_engine(user['uid'])
    Downloads and caches all available history for a ticker across all timeframes.
    Also saves stock metadata (sector, market cap, etc.)
    """
    raw_ticker = str(data.get("ticker", "")).strip().upper()
    if not raw_ticker:
        return {"error": "Ticker required"}
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", raw_ticker):
        return {"error": "Invalid ticker format"}
    ticker = raw_ticker
    
    timeframes = [
        ("30Sec", 7),
        ("1Min", 7),
        ("2Min", 14),
        ("3Min", 14),
        ("5Min", 60),
        ("10Min", 60),
        ("15Min", 60),
        ("30Min", 60),
        ("1Hour", 365),
        ("2Hour", 365),
        ("4Hour", 730),
        ("1Day", 1825)
    ]
    
    log = []
    end_date = get_now()
    
    # 1. Download Price History
    for tf, days in timeframes:
        start_date = end_date - timedelta(days=days)
        df = get_historical_data(ticker, tf, start_date, end_date)
        status = "Success" if df is not None and not df.empty else "No Data"
        log.append(f"{tf}: {status}")
        
    # 2. Download Metadata
    try:
        t = yf.Ticker(ticker)
        info = t.info
        
        # Security: Sanitize ticker to be strictly alphanumeric and verify path confinement
        clean_ticker = re.sub(r'[^A-Z0-9]', '', ticker)
        safe_ticker = os.path.basename(clean_ticker)
        data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
        if not os.path.exists(data_dir): 
            os.makedirs(data_dir)
        
        info_path = os.path.abspath(os.path.join(data_dir, f"{safe_ticker}_info.json"))
        if not info_path.startswith(data_dir + os.sep):
            return {"error": "Invalid ticker path"}
            
        with open(info_path, "w") as f:
            json.dump(info, f, indent=4)
        log.append("Metadata: Saved")
    except Exception as e:
        print(f"[api] Metadata extraction error: {e}")
        log.append("Metadata Error: Could not save metadata securely")
        
    return {"ticker": ticker, "status": log}


@app.get("/api/settings/indicators")
def get_indicator_settings(, user: dict = Depends(verify_token)):
    """Returns all indicator toggles grouped by category."""
    eng = user_manager.get_engine(user['uid'])
    return {
        "Momentum": {
            "ENABLE_RSI": {"label": "RSI", "description": "Relative Strength Index — Overbought / Oversold", "enabled": getattr(config, "ENABLE_RSI", True)},
            "ENABLE_MACD": {"label": "MACD", "description": "Moving Average Convergence Divergence", "enabled": getattr(config, "ENABLE_MACD", True)},
        },
        "Trend": {
            "ENABLE_EMA": {"label": "EMA Cross", "description": "Exponential Moving Average Crossover (9/21)", "enabled": getattr(config, "ENABLE_EMA", True)},
            "ENABLE_SUPERTREND": {"label": "Supertrend", "description": "Supertrend Indicator (10, 3)", "enabled": getattr(config, "ENABLE_SUPERTREND", True)},
            "ENABLE_BOLLINGER": {"label": "Bollinger", "description": "Bollinger Bands (20, 2σ)", "enabled": getattr(config, "ENABLE_BOLLINGER", True)},
            "ENABLE_ADX_TREND": {"label": "ADX Trend", "description": "Wilder's ADX (14-period) Trend Strength Filter", "enabled": getattr(config, "ENABLE_ADX_TREND", True)},
            "ENABLE_SMA": {"label": "SMA 200", "description": "Simple Moving Average (200-period) institutional filter", "enabled": getattr(config, "ENABLE_SMA", True)},
        },
        "Volume": {
            "ENABLE_VWAP": {"label": "VWAP", "description": "Volume Weighted Average Price", "enabled": getattr(config, "ENABLE_VWAP", True)},
        },
        "Custom": {
            "ENABLE_MYSTIC_PULSE": {"label": "Mystic Pulse", "description": "DMI-based Consecutive Trend Strength", "enabled": getattr(config, "ENABLE_MYSTIC_PULSE", True)},
            "ENABLE_AI_SENTIMENT": {"label": "News Sentiment", "description": "Groq-powered News Sentiment Analysis", "enabled": getattr(config, "ENABLE_AI_SENTIMENT", True)},
            "ENABLE_CANDLE_PATTERNS": {"label": "Candle Patterns", "description": "Engulfing, Hammer, Shooting Star patterns", "enabled": getattr(config, "ENABLE_CANDLE_PATTERNS", True)},
        },
    }


@app.post("/api/settings/risk")
async def update_risk_settings(settings: dict, user: dict = Depends(verify_token)):
    """Updates risk management parameters."""
    for key, value in settings.items():
        if hasattr(config, key):
            # Convert percentage strings/ints to decimals if needed
            if key in ['MAX_DAILY_DRAWDOWN', 'RISK_PER_TRADE', 'MAX_POSITION_PCT']:
                # Assume value is 0-100 if it's > 1
                if isinstance(value, (int, float)) and value > 1:
                    value = value / 100.0
            
    asyncio.create_task(save_settings_to_cloud())
    print(f"[settings] Updated risk parameters: {', '.join(settings.keys())}")
    return {"status": "success", "settings": settings}


@app.post("/api/settings/ticker_amount")
async def update_ticker_amount(data: dict, user: dict = Depends(verify_token)):
    """Updates the allocated trade amount for a specific ticker."""
    eng = user_manager.get_engine(user['uid'])
    ticker = data.get("ticker", "").upper()
    amount = data.get("amount")
    
    if ticker:
        if amount is None or amount == "":
            if ticker in eng.config.TICKER_AMOUNTS:
                del eng.config.TICKER_AMOUNTS[ticker]
        else:
            try:
                eng.config.TICKER_AMOUNTS[ticker] = float(amount)
            except:
                return {"status": "error", "message": "Invalid amount"}
        
        asyncio.create_task(save_settings_to_cloud())
        print(f"[settings] {ticker}: Allocated trade amount updated")
        return {"status": "success", "ticker_amounts": eng.config.TICKER_AMOUNTS}
    return {"status": "error", "message": "Ticker required"}


@app.post("/api/settings/timeframe")
async def update_timeframe(data: dict, user: dict = Depends(verify_token)):
    """Updates the default trading timeframe and triggers a re-scan."""
    global eng.latest_scans, eng.bot_scans, eng.latest_scans_by_tf, bot_scans_by_tf
    new_tf = data.get("timeframe")
    if new_tf in ["30Sec", "1Min", "2Min", "3Min", "5Min", "10Min", "15Min", "30Min", "1Hour", "2Hour", "4Hour", "1Day"]:
        eng.config.DEFAULT_TIMEFRAME = new_tf
        # Clear stale UI/evaluation scans for the previous timeframe while keeping
        # raw indicator bar caches available per (ticker, timeframe).
        eng.latest_scans = {
            ticker: scan for ticker, scan in eng.latest_scans.items()
            if scan.get('timeframe') == new_tf
        }
        eng.bot_scans = {
            ticker: scan for ticker, scan in eng.bot_scans.items()
            if scan.get('timeframe') == new_tf
        }
        eng.latest_scans_by_tf = {
            tf: scans for tf, scans in eng.latest_scans_by_tf.items()
            if tf == new_tf
        }
        bot_scans_by_tf = {
            tf: scans for tf, scans in bot_scans_by_tf.items()
            if tf == new_tf
        }
        asyncio.create_task(save_settings_to_cloud())
        print(f"[settings] Global Timeframe synced to {new_tf}. Triggering immediate scan.")
        
        # Wake up the background loop
        if force_scan_trigger:
            force_scan_trigger.set()
        else:
            print("[settings] Trigger skip: loop not started.")
        asyncio.create_task(_warm_timeframe_scans(new_tf, limit=5))
        
        return {"status": "success", "timeframe": new_tf}
    return {"status": "error", "message": "Invalid timeframe"}


@app.get("/api/watchlist")
def get_watchlist(, user: dict = Depends(verify_token)):
    """Returns the current watchlist."""
    eng = user_manager.get_engine(user['uid'])
    return eng.config.WATCHLIST


@app.post("/api/watchlist")
async def add_to_watchlist(data: dict, user: dict = Depends(verify_token)):
    """Add a ticker to the watchlist."""
    ticker = data.get("ticker", "").upper()
    if ticker and ticker not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(ticker)
        asyncio.create_task(save_settings_to_cloud())
        print(f"[settings] Added {ticker} to watchlist")
    return {"status": "success", "watchlist": eng.config.WATCHLIST}


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, user: dict = Depends(verify_token)):
    """Remove a ticker from the watchlist."""
    eng = user_manager.get_engine(user['uid'])
    ticker = ticker.upper()
    if ticker in eng.config.WATCHLIST:
        eng.config.WATCHLIST.remove(ticker)
        # Deactivate bot if removed from watchlist
        if ticker in eng.config.TRADELIST:
            eng.config.TRADELIST.remove(ticker)
            print(f"[settings] {ticker} removed from watchlist & deactivated")
        else:
            print(f"[settings] Removed {ticker} from watchlist")
        asyncio.create_task(save_settings_to_cloud())
        if force_scan_trigger:
            force_scan_trigger.set()
    return {"status": "success", "watchlist": eng.config.WATCHLIST, "tradelist": eng.config.TRADELIST}


@app.get("/api/tradelist")
def get_tradelist(, user: dict = Depends(verify_token)):
    """Returns the current active trade list."""
    return eng.config.TRADELIST


@app.post("/api/tradelist")
async def add_to_tradelist(data: dict, user: dict = Depends(verify_token)):
    """Add a ticker to the active trade list."""
    eng = user_manager.get_engine(user['uid'])
    ticker = data.get("ticker", "").upper()
    timeframe = data.get("timeframe", eng.config.DEFAULT_TIMEFRAME)
    
    if ticker and ticker not in eng.config.TRADELIST:
        eng.config.TRADELIST.append(ticker)
        
        # LOCK IN TIMEFRAME: Save to ticker settings so it's sticky
        if ticker not in eng.config.TICKER_SETTINGS:
            eng.config.TICKER_SETTINGS[ticker] = {}
        eng.config.TICKER_SETTINGS[ticker]['timeframe'] = timeframe
        print(f"[settings] Activated {ticker} on locked {timeframe} timeframe")

        # Ensure it's also in watchlist so we can see it
        if ticker not in eng.config.WATCHLIST:
            eng.config.WATCHLIST.append(ticker)
        asyncio.create_task(save_settings_to_cloud())
        if force_scan_trigger:
            force_scan_trigger.set()
    return {"status": "success", "tradelist": eng.config.TRADELIST, "watchlist": eng.config.WATCHLIST}


@app.get("/api/debug/history")
async def debug_history(, user: dict = Depends(verify_token)):
    return {
    eng = user_manager.get_engine(user['uid'])
        "executed_trades_count": 0,
        "trade_log_count": len(eng.trade_log),
        "executed_trades": [],
        "eng.trade_log": eng.trade_log[:10]
    }

@app.delete("/api/tradelist/{ticker}")
async def remove_from_tradelist(ticker: str, user: dict = Depends(verify_token)):
    """Remove a ticker from the active trade list."""
    ticker = ticker.upper()
    if ticker in eng.config.TRADELIST:
        eng.config.TRADELIST.remove(ticker)
        asyncio.create_task(save_settings_to_cloud())
        print(f"[settings] Removed {ticker} from active tradelist (Bot Deactivated)")
    return {"status": "success", "tradelist": eng.config.TRADELIST}


@app.get("/api/search/{query}")
def search_symbols(query: str, user: dict = Depends(verify_token)):
    """Searches for tradeable assets using the modular eng.broker."""
    eng = user_manager.get_engine(user['uid'])
    if len(query) < 1:
        return []
    return eng.broker.search_assets(query)


@app.post("/api/bots/create")
async def create_bot(data: dict, user: dict = Depends(verify_token)):
    """Creates a new active bot with custom settings (adds to watchlist, tradelist, and TICKER_SETTINGS)."""
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return {"status": "error", "message": "Symbol is required"}
        
    # Apply custom settings
    if symbol not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[symbol] = {}
        
    if "capital" in data:
        eng.config.TICKER_SETTINGS[symbol]["amount"] = float(data["capital"])
        
    if "threshold" in data:
        eng.config.TICKER_SETTINGS[symbol]["min_buy_signals"] = int(data["threshold"])
        eng.config.TICKER_SETTINGS[symbol]["min_sell_signals"] = int(data["threshold"])
    
    if "timeframe" in data:
        eng.config.TICKER_SETTINGS[symbol]["timeframe"] = data["timeframe"]
        
    if "sell_mode" in data:
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = data["sell_mode"]
    else:
        # Explicit default for new bots
        eng.config.TICKER_SETTINGS[symbol]["sell_mode"] = "indicator"
        
    if "indicators" in data and isinstance(data["indicators"], list):
        eng.config.TICKER_SETTINGS[symbol]["indicators"] = data["indicators"]

    # Risk Management overrides
    for rk in ["risk_per_trade", "max_daily_drawdown", "max_position_pct", "atr_stop_multiplier", "atr_trail_multiplier", "take_profit_multiplier"]:
        if rk in data:
            eng.config.TICKER_SETTINGS[symbol][rk] = float(data[rk])

    # Ensure it's in watchlist to be scanned
    if symbol not in eng.config.WATCHLIST:
        eng.config.WATCHLIST.append(symbol)
        
    # Add to tradelist to activate the bot
    if symbol not in eng.config.TRADELIST:
        eng.config.TRADELIST.append(symbol)
        print(f"[settings] Launched new bot for {symbol} with custom settings")
        
    # Save settings to cloud (runs in background)
    asyncio.create_task(save_settings_to_cloud())
    
    # Trigger immediate scan so user sees results right away
    if force_scan_trigger:
        force_scan_trigger.set()
    
    return {"status": "success", "symbol": symbol, "watchlist": eng.config.WATCHLIST, "tradelist": eng.config.TRADELIST, "settings": eng.config.TICKER_SETTINGS[symbol]}




@app.post("/api/cancel_order")
async def cancel_order(data: dict, user: dict = Depends(verify_token)):
    """Cancel an active order on Alpaca by ID."""
    eng = user_manager.get_engine(user['uid'])
    order_id = data.get("order_id")
    if not order_id:
        return {"status": "error", "message": "order_id is required"}
    result = eng.broker.cancel_order_by_id(order_id)
    if result.get("success"):
        return {"status": "success", "message": f"Order {order_id} cancelled successfully."}
    else:
        return {"status": "error", "message": result.get("error", "Failed to cancel order.")}





# ──────────────────────────────────────────────
# Legacy persistence removed (Now using Cloud Vault)
# ──────────────────────────────────────────────


@app.post("/api/settings/indicators")
async def update_indicators(updates: dict, user: dict = Depends(verify_token)):
    """Update indicator toggles or parameters dynamically. Instant in-memory + persists to Firestore."""
    for k, v in updates.items():
        if hasattr(config, k):
            if k.startswith("ENABLE_"):
                setattr(config, k, bool(v))
            else:
                try:
                    current_val = getattr(config, k)
                    if isinstance(current_val, int):
                        setattr(config, k, int(v))
                    elif isinstance(current_val, float):
                        setattr(config, k, float(v))
                    else:
                        setattr(config, k, v)
                except Exception as e:
                    print(f"[settings] Type conversion error for {k}: {e}")
                    setattr(config, k, v)

    # Save to Firestore (Does not trigger uvicorn reload)
    asyncio.create_task(save_settings_to_cloud())
    
    # Clear cache so the next dashboard fetch gets the new indicator states
    clear_evaluation_cache()
    if force_scan_trigger:
        force_scan_trigger.set()
        
    print(f"[settings] Updated indicator settings: {', '.join(updates.keys())}")
    return {"status": "success"}


@app.post("/api/settings/ticker")
async def update_ticker_settings(data: dict, user: dict = Depends(verify_token)):
    """Update settings for a specific ticker."""
    eng = user_manager.get_engine(user['uid'])
    ticker = data.get("ticker", "").upper()
    settings = data.get("settings", {})
    if not ticker: return {"status": "error"}

    if ticker not in eng.config.TICKER_SETTINGS:
        eng.config.TICKER_SETTINGS[ticker] = {}
    # Filter out null values to keep global defaults if not specified
    for k, v in settings.items():
        if v is not None:
            eng.config.TICKER_SETTINGS[ticker][k] = v
        elif k in eng.config.TICKER_SETTINGS[ticker]:
            del eng.config.TICKER_SETTINGS[ticker][k]
    
    asyncio.create_task(save_settings_to_cloud())
    return {"status": "success"}

@app.delete("/api/settings/ticker/{ticker}")
async def reset_ticker_settings(ticker: str, user: dict = Depends(verify_token)):
    """Reset a ticker to global defaults."""
    ticker = ticker.upper()
    if ticker in eng.config.TICKER_SETTINGS:
        del eng.config.TICKER_SETTINGS[ticker]
        asyncio.create_task(save_settings_to_cloud())
    return {"status": "success"}

def _load_saved_settings(, user: dict = Depends(verify_token)):
    """Load indicator toggles from settings.json on startup."""
    eng = user_manager.get_engine(user['uid'])
    import json, os
    settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k == "WATCHLIST":
                    eng.config.WATCHLIST = v
                elif k == "TRADELIST":
                    eng.config.TRADELIST = v
                elif hasattr(config, k) and k.startswith("ENABLE_"):
                    setattr(config, k, bool(v))
            # Load Ticker Settings
            eng.config.TICKER_SETTINGS = saved.get("TICKER_SETTINGS", {})
            eng.config.DEFAULT_TIMEFRAME = saved.get("DEFAULT_TIMEFRAME", eng.config.DEFAULT_TIMEFRAME)
            eng.config.SCAN_INTERVAL_SECONDS = saved.get("SCAN_INTERVAL_SECONDS", eng.config.SCAN_INTERVAL_SECONDS)
            
            print(f"[settings] Loaded {len(saved)} saved settings")
        except Exception as e:
            print(f"[settings] Error loading: {e}")


# Legacy _save_executed_trade / _load_executed_trades removed.
# Order history is now fetched live from Alpaca via eng.broker.get_order_history().

# Load initial settings (Now handled by lifespan on startup)

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _format_trade_for_ui(trade: dict) -> dict:
    """Formats a trade decision for the frontend table."""
    # Resolve timeframe: entry's own > ticker settings > global default
    ticker = trade.get("ticker", "").replace("/", "").upper()
    tf = trade.get("timeframe")
    if not tf:
        t_settings = getattr(config, 'TICKER_SETTINGS', {}).get(ticker, {})
        tf = t_settings.get('timeframe', eng.config.DEFAULT_TIMEFRAME)
    
    return {
        "time": trade.get("time", ""),
        "action": trade.get("action", ""),
        "ticker": ticker,
        "price": trade.get("price", ""),
        "qty": trade.get("qty", "N/A"),
        "total_cost": trade.get("total_cost", 0),
        "fees": trade.get("fees", 0),
        "reason": trade.get("reason", ""),
        "pl": trade.get("pl"),
        "pl_pct": trade.get("pl_pct"),
        "bullish_count": trade.get("bullish_count", 0),
        "bearish_count": trade.get("bearish_count", 0),
        "total_signals": trade.get("total_signals", 0),
        "timeframe": tf,
        "log_type": trade.get("log_type", "Active Bot"),
    }



def _format_scan_for_ui(scan: dict) -> dict:
    """Formats a full scan result for the watchlist panel.
    eng = user_manager.get_engine(user['uid'])
    Sends ALL signals with an 'enabled' flag so the UI can
    show disabled signals as greyed-out clickable cards."""

    SIGNAL_TO_TOGGLE = {
        'RSI': 'ENABLE_RSI',
        'MACD': 'ENABLE_MACD',
        'EMA Cross': 'ENABLE_EMA',
        'Supertrend': 'ENABLE_SUPERTREND',
        'Bollinger': 'ENABLE_BOLLINGER',
        'VWAP': 'ENABLE_VWAP',
        'Mystic Pulse': 'ENABLE_MYSTIC_PULSE',
        'News Sentiment': 'ENABLE_AI_SENTIMENT',
        'Candle Patterns': 'ENABLE_CANDLE_PATTERNS',
        'ADX Trend': 'ENABLE_ADX_TREND',
        'SMA': 'ENABLE_SMA',
    }

    raw_signals = scan.get("signals", {})
    all_signals = {}
    for name, data in raw_signals.items():
        toggle_key = SIGNAL_TO_TOGGLE.get(name, '')
        enabled = getattr(config, toggle_key, True) if toggle_key else True
        all_signals[name] = {**data, 'enabled': enabled, 'toggle_key': toggle_key}

    bullish = sum(s.get('weight', 1) for s in all_signals.values() if s.get('signal') == 'BULLISH' and s.get('enabled'))
    bearish = sum(s.get('weight', 1) for s in all_signals.values() if s.get('signal') == 'BEARISH' and s.get('enabled'))
    active_count = sum(1 for s in all_signals.values() if s.get('enabled'))

    return {
        "ticker": scan.get("ticker", ""),
        "price": scan.get("price", ""),
        "action": scan.get("action", "HOLD"),
        "reason": scan.get("reason", ""),
        "sentiment_score": scan.get("sentiment_score", 0),
        "sentiment_confidence": scan.get("sentiment_confidence", 0),
        "sentiment_summary": scan.get("sentiment_summary", ""),
        "sentiment_key_factor": scan.get("sentiment_key_factor", "N/A"),
        "bullish_count": bullish,
        "bearish_count": bearish,
        "total_signals": active_count,
        "signals": all_signals,
        "rsi": scan.get("rsi", 0),
        "atr": scan.get("atr", 0),
        "pl": scan.get("pl"),
        "pl_pct": scan.get("pl_pct"),
        "qty": scan.get("qty"),
        "position_sizing": scan.get("position_sizing", {}),
        "price_history": scan.get("price_history", []),
    }
