"""Independent per-user state for the market screener.

The screener deliberately does not construct ``UserEngine`` and never starts the
trading loop.  It shares the analysis stack through ``UserConfig`` only; its
profiles, page settings, scan history, and scheduler lifecycle are separate from
Bots and Dashboard state.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from user_config import UserConfig


_SYMBOL_RE = re.compile(r"[A-Z0-9.\-]{1,15}")
_SMS_PHONE_RE = re.compile(r"\+[1-9]\d{7,14}$")
_SIGNAL_DIRECTIONS = {"BULLISH", "BEARISH", "NEUTRAL"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def normalize_screener_symbol(value: Any) -> str:
    symbol = str(value or "").upper().replace("/", "").strip()
    return symbol if _SYMBOL_RE.fullmatch(symbol) else ""


def normalize_screener_sms_phone(value: Any) -> str:
    """Normalize an SMS destination to E.164 without logging personal data."""
    phone = re.sub(r"[\s().-]", "", str(value or "").strip())
    if phone.startswith("00"):
        phone = f"+{phone[2:]}"
    return phone if _SMS_PHONE_RE.fullmatch(phone) else ""


class ScreenerUserState:
    """State owned exclusively by one user's Screener page."""

    def __init__(self, uid: str, db):
        self.uid = uid
        self.db = db
        self.config = UserConfig(uid)
        self.profiles: dict[str, dict] = {}
        self.order: list[str] = []
        self.latest_scans: dict[str, dict] = {}
        self.news_watch: dict[str, dict] = {}
        self.earnings_watch: dict[str, dict] = {}
        self.fundamental_reports: dict[str, dict] = {}
        self.activity: list[dict] = []
        self.evaluation_state: dict[str, dict] = {}
        self.notifications: list[dict] = []
        self.email_outbox: list[dict] = []
        self.sms_outbox: list[dict] = []
        self.notification_settings: dict[str, Any] = {
            "email_enabled": False,
            "sms_enabled": False,
            "sms_phone_number": "",
            "bullish_enabled": True,
            "bearish_enabled": True,
            "cooldown_minutes": 60,
        }
        self.health: dict[str, Any] = {
            "status": "starting",
            "last_scan_at": None,
            "last_success_at": None,
            "last_error": "",
            "consecutive_failures": 0,
            "email_queued": 0,
            "email_failed": 0,
            "sms_queued": 0,
            "sms_failed": 0,
        }
        self.page_settings: dict[str, Any] = {
            "scan_interval_seconds": 60,
            "chart_timeframe": self.config.DEFAULT_TIMEFRAME,
            "chart_session": "regular",
        }
        self.loaded = False
        self.load_lock = asyncio.Lock()
        self.save_lock = asyncio.Lock()
        self.scan_lock = asyncio.Lock()
        self.news_lock = asyncio.Lock()
        self.earnings_lock = asyncio.Lock()
        self.fundamental_lock = asyncio.Lock()
        self.force_scan = asyncio.Event()
        self._last_cloud_digest: str | None = None
        self._cloud_retry_at = 0.0
        self._cloud_failures = 0

    @property
    def force_scan_trigger(self):
        """Compatibility with shared settings handlers; no trading loop involved."""
        return self.force_scan

    async def save_settings(self) -> None:
        await self.save()

    @property
    def local_path(self) -> str:
        return os.path.join("data", f"screener_settings_{self.uid}.json")

    def _apply(self, raw: dict) -> None:
        profiles = raw.get("profiles", raw.get("ticker_settings", {}))
        clean_profiles: dict[str, dict] = {}
        if isinstance(profiles, dict):
            for raw_symbol, raw_settings in profiles.items():
                symbol = normalize_screener_symbol(raw_symbol)
                if not symbol or not isinstance(raw_settings, dict):
                    continue
                settings = dict(raw_settings)
                settings["source"] = "screener"
                settings.setdefault("enabled", not bool(settings.get("paused", False)))
                clean_profiles[symbol] = settings

        requested_order = raw.get("order", raw.get("tradelist", []))
        ordered = []
        if isinstance(requested_order, list):
            for item in requested_order:
                symbol = normalize_screener_symbol(item)
                if symbol in clean_profiles and symbol not in ordered:
                    ordered.append(symbol)
        ordered.extend(symbol for symbol in clean_profiles if symbol not in ordered)

        contexts = raw.get("contexts", {})
        screener_context = contexts.get("screener", {}) if isinstance(contexts, dict) else {}
        if isinstance(screener_context, dict):
            self.config.contexts["screener"] = {
                "toggles": dict(screener_context.get("toggles", {}) or {}),
                "parameters": dict(screener_context.get("parameters", {}) or {}),
            }

        page_settings = raw.get("page_settings", {})
        if isinstance(page_settings, dict):
            self.page_settings.update(page_settings)

        scans = raw.get("latest_scans", {})
        if isinstance(scans, dict):
            self.latest_scans = {
                symbol: scan for symbol, scan in scans.items()
                if normalize_screener_symbol(symbol) in clean_profiles and isinstance(scan, dict)
            }
        news_watch = raw.get("news_watch", {})
        if isinstance(news_watch, dict):
            self.news_watch = {
                symbol: dict(value) for symbol, value in news_watch.items()
                if normalize_screener_symbol(symbol) in clean_profiles and isinstance(value, dict)
            }
        earnings_watch = raw.get("earnings_watch", {})
        if isinstance(earnings_watch, dict):
            self.earnings_watch = {
                symbol: dict(value) for symbol, value in earnings_watch.items()
                if normalize_screener_symbol(symbol) in clean_profiles and isinstance(value, dict)
            }
        fundamental_reports = raw.get("fundamental_reports", {})
        if isinstance(fundamental_reports, dict):
            self.fundamental_reports = {
                symbol: dict(value) for symbol, value in fundamental_reports.items()
                if normalize_screener_symbol(symbol) in clean_profiles and isinstance(value, dict)
            }
        evaluation_state = raw.get("evaluation_state", {})
        if isinstance(evaluation_state, dict):
            self.evaluation_state = {
                symbol: dict(value) for symbol, value in evaluation_state.items()
                if normalize_screener_symbol(symbol) in clean_profiles and isinstance(value, dict)
            }
        notifications = raw.get("notifications", [])
        if isinstance(notifications, list):
            self.notifications = [item for item in notifications[:1000] if isinstance(item, dict)]
        outbox = raw.get("email_outbox", [])
        if isinstance(outbox, list):
            self.email_outbox = [item for item in outbox[:1000] if isinstance(item, dict)]
        sms_outbox = raw.get("sms_outbox", [])
        if isinstance(sms_outbox, list):
            self.sms_outbox = [item for item in sms_outbox[:1000] if isinstance(item, dict)]
        notification_settings = raw.get("notification_settings", {})
        if isinstance(notification_settings, dict):
            self.notification_settings.update(notification_settings)
        health = raw.get("health", {})
        if isinstance(health, dict):
            self.health.update(health)
        activity = raw.get("activity", [])
        if isinstance(activity, list):
            self.activity = [item for item in activity[:100] if isinstance(item, dict)]

        self.profiles = clean_profiles
        self.order = ordered
        # Analysis helpers read ticker-specific values from UserConfig.
        self.config.TICKER_SETTINGS = self.profiles
        self.profiles = self.config._ticker_settings
        self.config.TRADELIST = []
        self.config.WATCHLIST = []

    def payload(self) -> dict:
        self.prune_notifications()
        payload = {
            "schema_version": "1.0",
            "profiles": self.profiles,
            "order": self.order,
            "contexts": {"screener": self.config.contexts.get("screener", {})},
            "page_settings": self.page_settings,
            "latest_scans": self.latest_scans,
            "news_watch": self.news_watch,
            "earnings_watch": self.earnings_watch,
            "fundamental_reports": self.fundamental_reports,
            "activity": self.activity[:100],
            "evaluation_state": self.evaluation_state,
            "notifications": self.notifications[:1000],
            "email_outbox": self.email_outbox[:1000],
            "sms_outbox": self.sms_outbox[:1000],
            "notification_settings": self.notification_settings,
            "health": self.health,
            "saved_at": time.time(),
        }
        return json.loads(json.dumps(payload, default=str))

    def prune_notifications(self, now_ts: float | None = None) -> None:
        cutoff = (now_ts or time.time()) - 90 * 86400
        def is_recent(item: dict) -> bool:
            value = item.get("created_at", now_ts or time.time())
            try:
                if isinstance(value, str):
                    value = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                return float(value or 0) >= cutoff
            except (TypeError, ValueError, OverflowError):
                return False
        self.notifications = [
            item for item in self.notifications
            if is_recent(item)
        ][:1000]
        self.email_outbox = [
            item for item in self.email_outbox
            if is_recent(item)
        ][:1000]
        self.sms_outbox = [
            item for item in self.sms_outbox
            if is_recent(item)
        ][:1000]

    async def load(self) -> None:
        if self.loaded:
            return
        async with self.load_lock:
            if self.loaded:
                return
            cloud_data: dict = {}
            local_data: dict = {}
            try:
                from local_store import load_state
                local_data = await asyncio.to_thread(load_state, self.uid, "screener_state") or {}
            except Exception as exc:
                print(f"[screener] Local SQLite load unavailable: {exc}")
            if not local_data:
                try:
                    with open(self.local_path, "r", encoding="utf-8") as handle:
                        local_data = json.load(handle)
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    local_data = {}

            self._apply(local_data or {})
            self.loaded = True

    async def save(self) -> None:
        async with self.save_lock:
            data = self.payload()
            try:
                os.makedirs("data", exist_ok=True)
                with open(self.local_path, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2, default=str)
            except OSError as exc:
                print(f"[screener] Local save failed: {exc}")

            try:
                from local_store import save_state
                sqlite_data = copy.deepcopy(data)
                for scan in (sqlite_data.get("latest_scans", {}) or {}).values():
                    if isinstance(scan, dict):
                        for heavy_key in ("price_history", "priceHistory", "raw_candles", "candles"):
                            scan.pop(heavy_key, None)
                await asyncio.to_thread(save_state, self.uid, "screener_state", sqlite_data)
            except Exception as exc:
                print(f"[screener] Local SQLite save failed: {exc}; state remains active in memory.")

    def profile_revision(self, symbol: str) -> str:
        profile = self.profiles.get(symbol, {})
        # Recompute from canonical technical inputs so profiles saved by older
        # versions do not treat a scheduling-only interval change as a new
        # technical baseline.
        return profile_revision(profile) if profile else "1"

    def mark_profile_baseline(self, symbol: str, reason: str) -> None:
        current = self.evaluation_state.setdefault(symbol, {})
        revision = self.profile_revision(symbol)
        old_revision = current.get("profile_revision")
        generation = int(current.get("generation") or 0)
        if old_revision and (old_revision != revision or reason in {"profile_activated", "profile_settings_changed"}):
            generation += 1
        current.update({
            "generation": str(generation or 1),
            "profile_revision": revision,
            "baseline_required": True,
            "baseline_reason": reason,
            "last_direction": None,
            "last_actionable_direction": None,
            "virtual_position": False,
            "virtual_entry_price": None,
            "virtual_entry_atr": None,
            "virtual_entry_candle_key": None,
            "virtual_peak_price": None,
            "virtual_trailing_stop": None,
            "last_exit_event_candle_key": None,
            "last_exit_event": None,
            "last_candle_key": None,
        })

    def commit_evaluation(self, symbol: str, evaluation: dict, now_ts: float | None = None) -> dict | None:
        """Commit one closed-candle evaluation and return a new notification, if any."""
        now_ts = now_ts or time.time()
        if evaluation.get("evaluation_status") != "valid":
            return None
        candle_meta = evaluation.get("candle_meta") or {}
        candle_key = str(candle_meta.get("candle_key") or "")
        if not candle_key:
            return None
        direction = str(evaluation.get("direction") or "NEUTRAL").upper()
        if direction not in _SIGNAL_DIRECTIONS:
            return None
        profile = self.profiles.get(symbol, {})
        state = self.evaluation_state.setdefault(symbol, {})
        revision = self.profile_revision(symbol)
        if state.get("profile_revision") != revision:
            prior_generation = int(state.get("generation") or 0)
            state.clear()
            state.update({"generation": str(prior_generation + 1), "profile_revision": revision, "baseline_required": True})
        if state.get("last_candle_key") == candle_key:
            # Keep a structural exit latched for the completed candle. A
            # repeated scan must not immediately reopen a bullish cycle from
            # the same candle after the virtual cycle has exited.
            if state.get("last_exit_event_candle_key") == candle_key and direction == "BULLISH":
                return None
            generation = str(state.get("generation") or 1)
            already_recorded = any(
                item.get("symbol") == symbol
                and str(item.get("generation") or "1") == generation
                and str(item.get("direction") or "").upper() == direction
                and str(item.get("candle_key") or "") == candle_key
                for item in self.notifications
            )
            # Permit one same-candle recovery when the directional alert was
            # evaluated but never persisted; normal scans stay deduplicated.
            if direction not in {"BULLISH", "BEARISH"} or already_recorded:
                return None

        previous = state.get("last_direction")
        state_update = {
            "profile_revision": revision,
            "last_candle_key": candle_key,
            "last_candle_open_ts": candle_meta.get("candle_open_ts"),
            "last_candle_close_ts": candle_meta.get("candle_close_ts"),
            "last_direction": direction,
            "last_evaluated_at": now_ts,
            "last_status": "valid",
            "baseline_required": False,
        }
        if state.get("last_exit_event_candle_key") and state.get("last_exit_event_candle_key") != candle_key:
            state_update["last_exit_event_candle_key"] = None
            state_update["last_exit_event"] = None
        # This is a virtual signal lifecycle, not a brokerage position. It lets
        # the independent Screener alternate between bullish-entry and
        # bearish-exit watches without reading Bots or account holdings.
        if direction in {"BULLISH", "BEARISH"}:
            state_update["last_actionable_direction"] = direction
            state_update["virtual_position"] = direction == "BULLISH"
        # A Screener cycle is analysis-only, but a virtual entry reference is
        # needed to evaluate its configured exit levels truthfully. Capture it
        # only when a bullish cycle starts; do not move the reference on each
        # subsequent bullish candle.
        if direction == "BULLISH" and not bool(state.get("virtual_position")):
            state_update.update({
                "virtual_entry_price": _safe_float(evaluation.get("price_raw"), 0.0) or None,
                "virtual_entry_atr": _safe_float(evaluation.get("atr"), 0.0) or None,
                "virtual_entry_candle_key": candle_key,
                "virtual_peak_price": _safe_float(evaluation.get("price_raw"), 0.0) or None,
                "virtual_trailing_stop": None,
            })
        elif bool(state.get("virtual_position")) and direction != "BEARISH":
            price = _safe_float(evaluation.get("price_raw"), 0.0)
            if price > 0:
                state_update["virtual_peak_price"] = max(_safe_float(state.get("virtual_peak_price"), 0.0), price)
        elif direction == "BEARISH":
            state_update.update({
                "virtual_entry_price": None,
                "virtual_entry_atr": None,
                "virtual_entry_candle_key": None,
                "virtual_peak_price": None,
                "virtual_trailing_stop": None,
            })
        if evaluation.get("screener_exit_event"):
            state_update["last_exit_event_candle_key"] = candle_key
            state_update["last_exit_event"] = copy.deepcopy(evaluation.get("atr_levels") or {})
        state.update(state_update)
        # A first valid directional candle is an actionable Screener alert.
        # The baseline flag still prevents duplicate work after profile resets,
        # but it must not hide the first setup from Alerts or Signal Activity.
        if direction == "NEUTRAL":
            return None
        if previous == direction:
            generation = str(state.get("generation") or 1)
            has_generation_alert = any(
                item.get("symbol") == symbol
                and str(item.get("generation") or "1") == generation
                and str(item.get("direction") or "").upper() == direction
                for item in self.notifications
            )
            # Recover a directional setup that was evaluated before alert
            # persistence was fixed, then resume normal same-direction dedupe.
            if has_generation_alert:
                return None

        opposite_transition = previous in {"BULLISH", "BEARISH"} and direction in {"BULLISH", "BEARISH"} and previous != direction
        direction_enabled = bool(profile.get(
            f"alert_{direction.lower()}_enabled",
            self.notification_settings.get(f"{direction.lower()}_enabled", True),
        ))
        last_alert_at = float(state.get(f"last_{direction.lower()}_alert_at") or 0)
        raw_cooldown = profile.get(
            "alert_cooldown_minutes",
            self.notification_settings.get("cooldown_minutes", 60),
        )
        try:
            # Zero is a deliberate setting: it disables the alert throttle.
            # Do not use ``or 60`` here because that turns an explicitly
            # disabled cooldown back into the default one-hour cooldown.
            cooldown_minutes = max(0, int(raw_cooldown)) if raw_cooldown is not None else 60
        except (TypeError, ValueError):
            cooldown_minutes = 60
        cooldown = cooldown_minutes * 60
        cooldown_active = not opposite_transition and last_alert_at > 0 and (now_ts - last_alert_at) < cooldown
        if not direction_enabled or cooldown_active:
            return None

        generation = str(state.get("generation") or 1)
        notification_id = hashlib.sha256("|".join([
            self.uid, generation, symbol, revision,
            str(profile.get("timeframe") or ""), str(profile.get("market_session") or ""), candle_key, direction,
        ]).encode()).hexdigest()
        if any(item.get("id") == notification_id for item in self.notifications):
            return None
        if profile.get("options_trading"):
            play_type = "CALL" if direction == "BULLISH" else "PUT"
            exp_days = profile.get("options_expiry_days", 3)
            strike_type = profile.get("options_strike_type", "ATM")
            title = f"{symbol} {play_type} Setup (~{exp_days}D {strike_type})"
        else:
            title = f"{symbol} {direction.title()} setup"
        email_enabled = bool(profile.get("email_enabled", self.notification_settings.get("email_enabled")))
        sms_enabled = bool(self.notification_settings.get("sms_enabled"))
        sms_phone_number = normalize_screener_sms_phone(self.notification_settings.get("sms_phone_number"))
        notification = {
            "id": notification_id,
            "uid": self.uid,
            "symbol": symbol,
            "direction": direction,
            "title": title,
            "created_at": now_ts,
            "candle_key": candle_key,
            "candle_open_ts": candle_meta.get("candle_open_ts"),
            "candle_close_ts": candle_meta.get("candle_close_ts"),
            "profile_revision": revision,
            "generation": generation,
            "price": evaluation.get("price_raw"),
            "reason": evaluation.get("reason", ""),
            "signals": evaluation.get("signals", {}),
            "sentiment": {
                "score": evaluation.get("sentiment_score"),
                "confidence": evaluation.get("sentiment_confidence"),
                "direction": evaluation.get("news_sentiment_direction"),
            },
            "atr": evaluation.get("atr"),
            "atr_levels": evaluation.get("atr_levels", {}),
            "read": False,
            # Browser/in-app is the durable notification record and is always
            # created when the selected directional alert is eligible.
            "browser_status": "delivered",
            "email_status": "queued" if email_enabled else "not_requested",
            "sms_status": "queued" if sms_enabled and sms_phone_number else ("setup_required" if sms_enabled else "not_requested"),
        }
        self.notifications.insert(0, notification)
        self.notifications = self.notifications[:1000]
        state[f"last_{direction.lower()}_alert_at"] = now_ts
        if notification["email_status"] == "queued":
            self.email_outbox.append({
                "id": notification_id,
                "notification_id": notification_id,
                "status": "queued",
                "attempts": 0,
                "next_attempt_at": now_ts,
                "created_at": now_ts,
            })
            self.email_outbox = self.email_outbox[-1000:]
        if notification["sms_status"] == "queued":
            self.sms_outbox.append({
                "id": notification_id,
                "notification_id": notification_id,
                "to": sms_phone_number,
                "status": "queued",
                "attempts": 0,
                "next_attempt_at": now_ts,
                "created_at": now_ts,
            })
            self.sms_outbox = self.sms_outbox[-1000:]
        print(
            f"[screener] Alert created: {symbol} {direction} "
            f"candle_key={candle_key} candle_close_ts={candle_meta.get('candle_close_ts')} "
            f"created_at={now_ts}"
        )
        return notification


def profile_revision(settings: dict) -> str:
    ignored = {
        "revision", "profile_revision", "started_at", "enabled", "paused",
        # Scan cadence changes scheduling only; they do not change the
        # technical inputs or invalidate the completed-candle baseline.
        "scan_interval_seconds",
        "alert_preferences", "alert_bullish_enabled", "alert_bearish_enabled",
        "alert_cooldown_minutes", "news_watch_enabled", "earnings_watch_enabled", "fundamental_watch_enabled",
        "symbol", "ticker",
    }
    clean = {
        key: value for key, value in (settings or {}).items()
        if not str(key).startswith("_") and key not in ignored
    }
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()[:16]


def evaluate_screener_profile(uid: str, settings: dict, context_settings: dict | None = None) -> dict:
    """Evaluate an immutable profile snapshot without an engine, broker, or positions."""
    from user_config import active_evaluation_context, active_ticker_context, get_user_config, set_user_config
    from trader import evaluate_trade

    symbol = normalize_screener_symbol(settings.get("symbol") or settings.get("ticker"))
    if not symbol:
        return {"evaluation_status": "error", "reason": "Invalid symbol"}
    config = UserConfig(uid)
    config.contexts["screener"] = copy.deepcopy(context_settings or config.contexts["screener"])
    snapshot = copy.deepcopy(settings)
    snapshot["source"] = "screener"
    snapshot["enabled"] = True
    config.TICKER_SETTINGS = {symbol: snapshot}
    config.TRADELIST = []
    user_token = set_user_config(config)
    eval_token = active_evaluation_context.set("screener")
    ticker_token = active_ticker_context.set(symbol)
    try:
        result = evaluate_trade(
            symbol,
            100000.0,
            100000.0,
            snapshot.get("timeframe") or config.DEFAULT_TIMEFRAME,
            "webull",
            True,
            True,
            False,
        ) or {}
        if result.get("evaluation_status") != "valid" or not result.get("candle_meta", {}).get("candle_key"):
            result["evaluation_status"] = "waiting"
            return result
        result["direction"] = {"BUY": "BULLISH", "SELL": "BEARISH"}.get(result.get("action"), "NEUTRAL")
        result["profile_revision"] = profile_revision(snapshot)
        atr = float(result.get("atr") or 0)
        price = float(result.get("price_raw") or 0)
        buy_mode = str(snapshot.get("buy_mode") or "indicator").strip().lower()
        sell_mode = str(snapshot.get("sell_mode") or snapshot.get("options_exit_strategy") or "indicator").strip().lower()
        active_mode = buy_mode if result["direction"] == "BULLISH" else sell_mode
        atr_modes = {"atr", "hybrid", "hybrid_atr", "sltp"}
        if active_mode in atr_modes and atr > 0 and price > 0:
            is_entry_atr = result["direction"] == "BULLISH" and active_mode in {"atr", "hybrid_atr"}
            stop_mult = _safe_float(
                snapshot.get("entry_atr_multiplier") if is_entry_atr else snapshot.get("atr_stop_multiplier"),
                2.0,
            )
            target_mult = _safe_float(
                snapshot.get("take_profit_multiplier", snapshot.get("atr_take_profit_multiplier")),
                4.0,
            )
            stop_mult = stop_mult if stop_mult > 0 else 2.0
            target_mult = target_mult if target_mult > 0 else 4.0
            result["atr_levels"] = {
                "stop": price - stop_mult * atr if result["direction"] == "BULLISH" else price + stop_mult * atr,
                "target": price + target_mult * atr if result["direction"] == "BULLISH" else price - target_mult * atr,
                "multiplier_stop": stop_mult,
                "multiplier_target": target_mult,
                "active_mode": active_mode,
            }
        else:
            result["atr_levels"] = {}
        return result
    except Exception as exc:
        return {"evaluation_status": "error", "reason": str(exc), "ticker": symbol}
    finally:
        active_ticker_context.reset(ticker_token)
        active_evaluation_context.reset(eval_token)
        from user_config import _active_user_config
        _active_user_config.reset(user_token)


class ScreenerManager:
    """Owns screener users without depending on the automated trading manager."""

    def __init__(self, db):
        self.db = db
        self.states: dict[str, ScreenerUserState] = {}
        # Track states that came from the active-user registry separately from
        # states created by a live API request. This lets registry refreshes
        # drop users that were deactivated without evicting a user who is
        # currently opening the Screener while the registry is unavailable.
        self._registry_uids: set[str] = set()
        # Firestore is only the discovery source. Keep the last successful
        # registry locally so a transient cloud/network outage cannot make the
        # independent screener scheduler lose its users.
        self._registry_cache_namespace = "screener_registry"
        self._registry_last_warning_at = 0.0
        self.evaluation_semaphore = asyncio.Semaphore(4)
        self.news_semaphore = asyncio.Semaphore(3)
        self.earnings_semaphore = asyncio.Semaphore(3)

    def get_state(self, uid: str) -> ScreenerUserState:
        uid = str(uid or '').strip()
        if not uid:
            raise ValueError("Screener user id is required")
        # A request authenticated by Firebase is the authoritative signal that
        # this local Screener state is active. This keeps discovery independent
        # from a separate Firestore registry query.
        self._registry_uids.add(uid)
        if uid not in self.states:
            self.states[uid] = ScreenerUserState(uid, self.db)
        return self.states[uid]

    async def discover_active_users(self) -> list[ScreenerUserState]:
        now = time.monotonic()

        async def _states_for(uids: set[str]) -> list[ScreenerUserState]:
            states = [self.get_state(uid) for uid in sorted(uids)]
            await asyncio.gather(*(state.load() for state in states), return_exceptions=True)
            return states

        async def _load_local_uids() -> set[str]:
            try:
                from local_store import list_state_users, load_state

                registry_task = asyncio.to_thread(
                    load_state, "__screener__", self._registry_cache_namespace
                )
                state_users_task = asyncio.to_thread(list_state_users, "screener_state")
                payload, state_users = await asyncio.gather(registry_task, state_users_task)
                payload = payload or {}
                values = payload.get("uids", []) if isinstance(payload, dict) else []
                cached_uids = {str(uid).strip() for uid in values if str(uid).strip()}
                stored_uids = {
                    str(uid).strip() for uid in (state_users or []) if str(uid).strip()
                }
                return cached_uids | stored_uids
            except Exception as exc:
                # The in-memory registry remains a valid fallback if local
                # persistence is unavailable as well.
                if now - self._registry_last_warning_at >= 60:
                    self._registry_last_warning_at = now
                    print(f"[ScreenerManager] Local registry unavailable; using memory: {exc}")
                return set()

        # Screener state is local-first. Discover persisted users from SQLite
        # instead of polling Firestore every minute just to obtain document
        # ids. This removes the 503 transport failure path from the scheduler
        # while retaining the cached and in-memory registries as fallbacks.
        active_uids = await _load_local_uids()
        active_uids.update(self._registry_uids)
        for stale_uid in self._registry_uids - active_uids:
            self.states.pop(stale_uid, None)
        self._registry_uids = active_uids
        try:
            from local_store import save_state

            await asyncio.to_thread(
                save_state,
                "__screener__",
                self._registry_cache_namespace,
                {"uids": sorted(active_uids), "saved_at": time.time()},
            )
        except Exception as exc:
            if now - self._registry_last_warning_at >= 60:
                self._registry_last_warning_at = now
                print(f"[ScreenerManager] Local registry cache save unavailable: {exc}")
        return await _states_for(active_uids)
