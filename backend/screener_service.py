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
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from user_config import UserConfig
from screener_tasks import ScreenerWorkTracker
from screener_strategy import migrate_legacy_alert_source


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
    # Yahoo search represents USD crypto pairs with a dash (BTC-USD), while
    # the Screener's Webull data path expects the compact form (BTCUSD).
    # Preserve dashes for ordinary securities such as BRK-B.
    if re.fullmatch(r"[A-Z0-9]{1,12}-(?:USD|USDT|USDC)", symbol):
        symbol = symbol.replace("-", "")
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
            "browser_enabled": True,
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
            "candle_probe_count": 0,
            "same_candle_skips": 0,
            "full_evaluation_count": 0,
            "data_quality_rejections": 0,
            "price_probe_count": 0,
            "price_entry_trigger_count": 0,
            "price_trigger_count": 0,
            "price_data_rejections": 0,
            "price_watch_skips": 0,
            "last_price_watch_at": None,
            "persistence_status": "starting",
            "persistence_error": "",
            "email_queued": 0,
            "email_failed": 0,
            "sms_queued": 0,
            "sms_failed": 0,
        }
        self.page_settings: dict[str, Any] = {
            "scan_interval_seconds": 1800,
            "chart_timeframe": "30Min",
            "chart_session": "extended",
        }
        self.loaded = False
        self.load_lock = asyncio.Lock()
        self.save_lock = asyncio.Lock()
        self.scan_lock = asyncio.Lock()
        self.price_watch_lock = asyncio.Lock()
        self.news_lock = asyncio.Lock()
        self.earnings_lock = asyncio.Lock()
        self.fundamental_lock = asyncio.Lock()
        self.force_scan = asyncio.Event()
        self._last_cloud_digest: str | None = None
        self._cloud_retry_at = 0.0
        self._cloud_failures = 0
        self._process_scan_lock_fd = None
        self._process_scan_lock_path = os.path.join(
            "data",
            f".screener_scan_{hashlib.sha256(str(uid).encode()).hexdigest()[:24]}.lock",
        )

    @property
    def force_scan_trigger(self):
        """Compatibility with shared settings handlers; no trading loop involved."""
        return self.force_scan

    async def save_settings(self) -> None:
        await self.save()

    async def refresh_from_persistence(self) -> bool:
        """Reload the latest durable state after acquiring the process lock."""
        try:
            from local_store import load_state
            raw = await asyncio.to_thread(load_state, self.uid, "screener_state")
        except Exception as exc:
            self.health["persistence_status"] = "degraded"
            self.health["persistence_error"] = f"reload: {exc}"[:500]
            return False
        if not isinstance(raw, dict) or not raw:
            return False
        self._apply(raw)
        return True

    async def try_acquire_process_scan_lock(self) -> bool:
        """Coordinate scans across backend worker processes on one host."""
        if self._process_scan_lock_fd is not None:
            return False
        try:
            import fcntl
        except ImportError:
            # Windows deployments should use a single scheduler worker or a
            # distributed lease; keep the in-process lock as the fallback.
            return True

        def _acquire() -> bool:
            os.makedirs("data", exist_ok=True)
            handle = open(self._process_scan_lock_path, "a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                handle.close()
                return False
            self._process_scan_lock_fd = handle
            return True

        try:
            return await asyncio.to_thread(_acquire)
        except (OSError, IOError):
            # A lock-file failure must not stop analysis; the per-process
            # asyncio lock remains the safe fallback and health persistence
            # continues to report any subsequent save problem.
            return True

    async def release_process_scan_lock(self) -> None:
        handle = self._process_scan_lock_fd
        self._process_scan_lock_fd = None
        if handle is None:
            return
        try:
            import fcntl
        except ImportError:
            handle.close()
            return

        def _release() -> None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

        await asyncio.to_thread(_release)

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
                # News Summary is a universal Screener feed. Older persisted
                # profiles may still carry the removed opt-out flag, so
                # migrate them on load instead of leaving their news watcher
                # permanently disabled until the user re-saves the ticker.
                settings["news_watch_enabled"] = True
                # Alert-source model: derive/convert once on load. Only the
                # behaviour-changing legacy profiles change strategy_mode (and
                # therefore their revision); the rest gain a derived field that
                # profile_revision ignores, so no baseline is reset.
                migrate_legacy_alert_source(settings)
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
                normalize_screener_symbol(symbol): scan for symbol, scan in scans.items()
                if normalize_screener_symbol(symbol) in clean_profiles and isinstance(scan, dict)
            }
        news_watch = raw.get("news_watch", {})
        if isinstance(news_watch, dict):
            self.news_watch = {
                normalize_screener_symbol(symbol): dict(value) for symbol, value in news_watch.items()
                if normalize_screener_symbol(symbol) in clean_profiles and isinstance(value, dict)
            }
        earnings_watch = raw.get("earnings_watch", {})
        if isinstance(earnings_watch, dict):
            self.earnings_watch = {
                normalize_screener_symbol(symbol): dict(value) for symbol, value in earnings_watch.items()
                if normalize_screener_symbol(symbol) in clean_profiles and isinstance(value, dict)
            }
        fundamental_reports = raw.get("fundamental_reports", {})
        if isinstance(fundamental_reports, dict):
            self.fundamental_reports = {
                normalize_screener_symbol(symbol): dict(value) for symbol, value in fundamental_reports.items()
                if normalize_screener_symbol(symbol) in clean_profiles and isinstance(value, dict)
            }
        evaluation_state = raw.get("evaluation_state", {})
        if isinstance(evaluation_state, dict):
            self.evaluation_state = {
                normalize_screener_symbol(symbol): dict(value) for symbol, value in evaluation_state.items()
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
        # Snapshot mutable state synchronously; serialization and I/O happen
        # in save's worker, not on the FastAPI event loop.
        return copy.deepcopy(payload)

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
                def load_legacy():
                    try:
                        with open(self.local_path, "r", encoding="utf-8") as handle:
                            return json.load(handle)
                    except (FileNotFoundError, json.JSONDecodeError, OSError):
                        return {}
                local_data = await asyncio.to_thread(load_legacy)

            self._apply(local_data or {})
            if not local_data:
                # Screener has independent persistence from the trading
                # engine. A first-use account must also begin with every
                # indicator/AI activation toggle disabled, and that choice
                # must survive the first local-state save/reload.
                self.config.disable_indicator_toggles()
                self.config.contexts["screener"]["toggles"] = dict(self.config.toggles)
            self.loaded = True

    async def save(self) -> bool:
        """Persist without blocking the loop; report authoritative SQLite success."""
        async with self.save_lock:
            data = self.payload()
            save_errors = []
            def write_snapshot():
                os.makedirs("data", exist_ok=True)
                # Replace the snapshot atomically so a process interruption
                # cannot leave a truncated settings/alert file.
                fd, temp_path = tempfile.mkstemp(
                    prefix=f".screener_{self.uid}_", suffix=".tmp", dir="data"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(data, handle, indent=2, default=str)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, self.local_path)
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
            try:
                await asyncio.to_thread(write_snapshot)
            except Exception as exc:
                save_errors.append(f"local: {type(exc).__name__}")

            durable = False
            try:
                from local_store import save_state
                sqlite_data = copy.deepcopy(data)
                for scan in (sqlite_data.get("latest_scans", {}) or {}).values():
                    if isinstance(scan, dict):
                        for heavy_key in ("price_history", "priceHistory", "raw_candles", "candles"):
                            scan.pop(heavy_key, None)
                await asyncio.to_thread(save_state, self.uid, "screener_state", sqlite_data)
                durable = True
            except Exception as exc:
                save_errors.append(f"sqlite: {type(exc).__name__}")
            if save_errors:
                self.health["persistence_status"] = "degraded"
                self.health["persistence_error"] = "; ".join(save_errors)[:500]
            else:
                self.health["persistence_status"] = "healthy"
                self.health["persistence_error"] = ""
            return durable

    def profile_revision(self, symbol: str) -> str:
        profile = self.profiles.get(symbol, {})
        # Recompute from canonical technical inputs so profiles saved by older
        # versions do not treat a scheduling-only interval change as a new
        # technical baseline.
        context = self.config.contexts.get("screener", {})
        if profile and any(context.values()):
            return profile_revision({**profile, "analysis_context": context})
        return profile_revision(profile) if profile else "1"

    def mark_profile_baseline(self, symbol: str, reason: str) -> None:
        current = self.evaluation_state.setdefault(symbol, {})
        revision = self.profile_revision(symbol)
        old_revision = current.get("profile_revision")
        generation = int(current.get("generation") or 0)
        # A timeframe/settings change starts a new candle baseline. Keeping the
        # prior timeframe's observed timestamp would make a valid 30-minute or
        # 1-hour candle look older than the last 5-minute candle.
        last_observed_candle_key = None if reason in {"profile_activated", "profile_settings_changed"} else (
            current.get("last_observed_candle_key") or current.get("last_candle_key")
        )
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
            "virtual_exit_mode": None,
            "virtual_exit_settings": {},
            "virtual_exit_levels": {},
            "virtual_peak_price": None,
            "virtual_trailing_stop": None,
            "last_exit_event_candle_key": None,
            "last_exit_event": None,
            "last_price_event_key": None,
            "last_price_event": None,
            "last_price_event_at": 0,
            "last_price_entry_key": None,
            "last_price_entry_event": None,
            "last_price_entry_at": 0,
            "last_price_watch": {},
            "last_candle_key": None,
            "last_observed_candle_key": last_observed_candle_key,
            "last_bullish_alert_at": 0,
            "last_bearish_alert_at": 0,
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
        last_observed_candle_key = state.get("last_observed_candle_key") or state.get("last_candle_key")
        if state.get("profile_revision") != revision:
            prior_generation = int(state.get("generation") or 0)
            state.clear()
            state.update({
                "generation": str(prior_generation + 1),
                "profile_revision": revision,
                "baseline_required": True,
                "last_observed_candle_key": last_observed_candle_key,
                "last_bullish_alert_at": 0,
                "last_bearish_alert_at": 0,
            })
        current_candle_ts = _safe_float(candle_key.rsplit(":", 1)[-1], 0.0)
        if current_candle_ts <= 0:
            return None
        if candle_key_regressed(last_observed_candle_key, candle_key):
            # Never let a delayed provider response move the virtual lifecycle
            # backward or create a reversal from an older completed candle.
            return None
        if state.get("last_candle_key") == candle_key:
            # Keep a structural exit latched for the completed candle. A
            # repeated scan must not create a second alert for the same
            # directional evaluation.
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
        baseline_before = bool(state.get("baseline_required"))
        state_update = {
            "profile_revision": revision,
            "last_candle_key": candle_key,
            "last_candle_open_ts": candle_meta.get("candle_open_ts"),
            "last_candle_close_ts": candle_meta.get("candle_close_ts"),
            "last_direction": direction,
            "last_evaluated_at": now_ts,
            "last_status": "valid",
            "baseline_required": False,
            "last_observed_candle_key": candle_key,
        }
        if state.get("last_exit_event_candle_key") and state.get("last_exit_event_candle_key") != candle_key:
            state_update["last_exit_event_candle_key"] = None
            state_update["last_exit_event"] = None
        # These fields support optional analysis-only price-level monitoring;
        # they are not brokerage positions and must not gate independent
        # bullish/bearish directional alerts.
        if direction in {"BULLISH", "BEARISH"}:
            state_update["last_actionable_direction"] = direction
            state_update["virtual_position"] = direction == "BULLISH"
        # A Screener cycle is analysis-only, but a virtual entry reference is
        # needed to evaluate its configured exit levels truthfully. Capture it
        # only when an optional bullish price watch starts; do not move the
        # reference on each subsequent bullish candle.
        if direction == "BULLISH" and not bool(state.get("virtual_position")):
            state_update.update({
                "virtual_entry_price": _safe_float(evaluation.get("price_raw"), 0.0) or None,
                "virtual_entry_atr": _safe_float(evaluation.get("atr"), 0.0) or None,
                "virtual_entry_candle_key": candle_key,
                "virtual_exit_mode": str(
                    profile.get("sell_mode")
                    or profile.get("options_exit_strategy")
                    or "indicator"
                ).strip().lower(),
                "virtual_exit_settings": copy.deepcopy(profile),
                "virtual_exit_levels": copy.deepcopy(evaluation.get("screener_exit_snapshot") or {}),
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
                "virtual_exit_mode": None,
                "virtual_exit_settings": {},
                "virtual_exit_levels": {},
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
        preset_info = evaluation.get("strategy_preset")
        if isinstance(preset_info, dict):
            # A strategy publishes a STATE, so only a change of state is an
            # alert. This also skips the generic "recover an unpersisted alert"
            # path below, which would re-announce a trend that was seeded silently.
            if str(preset_info.get("kind") or "") == "allocation":
                # A Malik allocation strategy alerts on a change of its confirmed regime
                # (completed daily closes only), including changes inside one direction
                # such as Bull rider -> Momentum trim. The first evaluation is recorded silently.
                regime = str(preset_info.get("regime") or "")
                previous_regime = state.get("last_allocation_regime")
                state["last_allocation_regime"] = regime or previous_regime
                if not regime or previous_regime is None or baseline_before or previous_regime == regime:
                    return None
            elif previous == direction:
                return None
            elif previous is None or baseline_before:
                # First evaluation: record the current state silently. Only a
                # signal that just happened on the latest completed candle is
                # announced; a trend that began weeks ago is not a new BUY.
                age = preset_info.get("signal_age_bars")
                if age is None or age > 0:
                    return None
        allocation_alert = isinstance(preset_info, dict) and str(preset_info.get("kind") or "") == "allocation"
        # Event-Driven: a different qualifying headline (a new catalyst) in the same direction is a new alert, subject
        # to the normal cooldown, so the first headline does not silence every later one until the tone flips. A profile
        # with no recorded catalyst yet (older profiles, a fresh baseline) is not treated as new, so nothing is re-sent.
        event_info = evaluation.get("event_qualification") if isinstance(evaluation.get("event_qualification"), dict) else {}
        catalyst_id = str(event_info.get("catalyst_id") or "") if str(profile.get("alert_source") or "") == "event" else ""
        last_catalyst_id = str(state.get("last_event_catalyst_id") or "")
        new_catalyst = bool(catalyst_id and last_catalyst_id and catalyst_id != last_catalyst_id)
        if catalyst_id and not last_catalyst_id and previous == direction:
            # Adopt the catalyst this profile is already acting on (an alert from before catalysts were tracked) as the
            # reference, silently, so only later headlines count as new.
            state["last_event_catalyst_id"] = catalyst_id
        if previous == direction and not allocation_alert and not new_catalyst:
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
        if allocation_alert:
            opposite_transition = True  # a regime change is a new state, not a repeat to throttle
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
        elif isinstance(evaluation.get("strategy_preset"), dict) and str(evaluation["strategy_preset"].get("kind") or "") == "allocation":
            info = evaluation["strategy_preset"]
            title = (f"{symbol} {info.get('label') or 'Strategy'}: {info.get('regime_label') or info.get('regime')} "
                     f"· target {info.get('target_text') or 'cash'}")
        elif isinstance(evaluation.get("strategy_preset"), dict):
            preset_label = str(evaluation["strategy_preset"].get("label") or "Strategy")
            title = f"{symbol} {'BUY' if direction == 'BULLISH' else 'SELL (exit)'} signal: {preset_label}"
        else:
            title = f"{symbol} {direction.title()} setup"
        browser_enabled = bool(self.notification_settings.get("browser_enabled", True))
        email_enabled = bool(profile.get("email_enabled", self.notification_settings.get("email_enabled")))
        sms_enabled = bool(self.notification_settings.get("sms_enabled"))
        sms_phone_number = normalize_screener_sms_phone(self.notification_settings.get("sms_phone_number"))
        # If every delivery channel is disabled, avoid creating an alert that
        # cannot be delivered. Email/SMS-only alerts remain queued when the
        # browser/in-app channel is disabled.
        if not browser_enabled and not email_enabled and not sms_enabled:
            return None
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
            "settings_snapshot": copy.deepcopy(profile),
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
            "browser_status": "delivered" if browser_enabled else "disabled",
            "email_status": "queued" if email_enabled else "not_requested",
            "sms_status": "queued" if sms_enabled and sms_phone_number else ("setup_required" if sms_enabled else "not_requested"),
        }
        self.notifications.insert(0, notification)
        self.notifications = self.notifications[:1000]
        state[f"last_{direction.lower()}_alert_at"] = now_ts
        if catalyst_id:
            state["last_event_catalyst_id"] = catalyst_id
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

    def commit_price_entry(self, symbol: str, event: dict, now_ts: float | None = None) -> dict | None:
        """Commit one fresh custom-price entry without pretending it is a candle.

        The entry is still analysis-only. A bullish entry starts the virtual
        long cycle so the independent exit watcher can monitor its frozen
        levels; a bearish entry is retained as a directional alert only (for
        example, a PUT-oriented options profile).
        """
        now_ts = now_ts or time.time()
        if not isinstance(event, dict):
            return None
        symbol = normalize_screener_symbol(symbol)
        event_key = str(event.get("price_event_key") or "").strip()
        trigger_price = _safe_float(event.get("trigger_price"), 0.0)
        direction = str(event.get("event_direction") or "BULLISH").upper()
        if not symbol or not event_key or trigger_price <= 0 or direction not in _SIGNAL_DIRECTIONS - {"NEUTRAL"}:
            return None

        profile = self.profiles.get(symbol, {})
        state = self.evaluation_state.setdefault(symbol, {})
        if state.get("last_price_entry_key") == event_key:
            return None
        # A live entry is one transition per virtual cycle. Do not re-alert
        # while the bullish cycle is already active, even if a provider repeats
        # a quote below the configured target.
        if direction == "BULLISH" and bool(state.get("virtual_position")):
            return None

        revision = self.profile_revision(symbol)
        generation = str(state.get("generation") or 1)
        notification_id = hashlib.sha256("|".join([
            self.uid,
            generation,
            symbol,
            revision,
            "PRICE_ENTRY",
            event_key,
        ]).encode()).hexdigest()
        if any(item.get("id") == notification_id for item in self.notifications):
            state["last_price_entry_key"] = event_key
            return None

        event_snapshot = copy.deepcopy(event)
        event_snapshot.update({
            "event_type": "PRICE_ENTRY",
            "event_direction": direction,
            "observed_at": event.get("observed_at") or datetime.now(timezone.utc).isoformat(),
            "price_event_key": event_key,
        })
        entry_candle_key = str(
            state.get("last_observed_candle_key")
            or state.get("last_candle_key")
            or f"PRICE:{event_key}"
        )
        state_update = {
            "profile_revision": revision,
            "last_actionable_direction": direction,
            "last_direction": direction,
            "last_price_entry_key": event_key,
            "last_price_entry_event": event_snapshot,
            "last_price_entry_at": now_ts,
            "last_status": "price_entry",
        }
        if direction == "BULLISH":
            state_update.update({
                "virtual_position": True,
                "virtual_entry_price": trigger_price,
                "virtual_entry_atr": _safe_float(event.get("atr"), 0.0) or None,
                "virtual_entry_candle_key": entry_candle_key,
                "virtual_exit_mode": str(
                    profile.get("sell_mode")
                    or profile.get("options_exit_strategy")
                    or "indicator"
                ).strip().lower(),
                "virtual_exit_settings": copy.deepcopy(profile),
                "virtual_exit_levels": copy.deepcopy(event.get("exit_snapshot") or {}),
                "virtual_peak_price": trigger_price,
                "virtual_trailing_stop": None,
            })
        else:
            state_update.update({
                "virtual_position": False,
                "virtual_entry_price": None,
                "virtual_entry_atr": None,
                "virtual_entry_candle_key": None,
                "virtual_exit_mode": None,
                "virtual_exit_settings": {},
                "virtual_exit_levels": {},
                "virtual_peak_price": None,
                "virtual_trailing_stop": None,
            })
        state.update(state_update)

        direction_enabled = bool(profile.get(
            f"alert_{direction.lower()}_enabled",
            self.notification_settings.get(f"{direction.lower()}_enabled", True),
        ))
        raw_cooldown = profile.get(
            "alert_cooldown_minutes",
            self.notification_settings.get("cooldown_minutes", 60),
        )
        try:
            cooldown_minutes = max(0, int(raw_cooldown)) if raw_cooldown is not None else 60
        except (TypeError, ValueError):
            cooldown_minutes = 60
        last_alert_at = float(state.get(f"last_{direction.lower()}_alert_at") or 0)
        cooldown_active = last_alert_at > 0 and (now_ts - last_alert_at) < cooldown_minutes * 60
        browser_enabled = bool(self.notification_settings.get("browser_enabled", True))
        email_enabled = bool(profile.get("email_enabled", self.notification_settings.get("email_enabled")))
        sms_enabled = bool(self.notification_settings.get("sms_enabled"))
        sms_phone_number = normalize_screener_sms_phone(self.notification_settings.get("sms_phone_number"))
        if not direction_enabled or cooldown_active or (not browser_enabled and not email_enabled and not sms_enabled):
            return None

        strategy_label = str(event.get("strategy_label") or "Custom Price").strip()
        notification = {
            "id": notification_id,
            "uid": self.uid,
            "symbol": symbol,
            "direction": direction,
            "event_direction": direction,
            "event_type": "PRICE_ENTRY",
            "alert_kind": "PRICE_EVENT",
            "title": f"{symbol} {strategy_label} entry reached",
            "created_at": now_ts,
            "candle_key": entry_candle_key,
            "candle_open_ts": state.get("last_candle_open_ts"),
            "candle_close_ts": state.get("last_candle_close_ts"),
            "profile_revision": revision,
            "settings_snapshot": copy.deepcopy(profile),
            "generation": generation,
            "price": trigger_price,
            "reason": event.get("text") or f"{strategy_label} entry reached by live price.",
            "signals": {},
            "sentiment": {},
            "atr": event.get("atr"),
            "atr_levels": event.get("atr_levels") or {},
            "price_event": event_snapshot,
            "read": False,
            "browser_status": "delivered" if browser_enabled else "disabled",
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
            f"[screener] Price entry alert created: {symbol} {direction} "
            f"price={trigger_price} event_key={event_key} created_at={now_ts}"
        )
        return notification

    def commit_price_event(self, symbol: str, event: dict, now_ts: float | None = None) -> dict | None:
        """Commit one fresh live-price exit event for the virtual lifecycle.

        Price events are separate from completed-candle alerts. The event
        closes the virtual bullish cycle once, even if a quote is repeated or
        two workers observe the same level.
        """
        now_ts = now_ts or time.time()
        if not isinstance(event, dict):
            return None
        symbol = normalize_screener_symbol(symbol)
        event_key = str(event.get("price_event_key") or "").strip()
        trigger_price = _safe_float(event.get("trigger_price"), 0.0)
        if not symbol or not event_key or trigger_price <= 0:
            return None

        profile = self.profiles.get(symbol, {})
        state = self.evaluation_state.setdefault(symbol, {})
        if not bool(state.get("virtual_position")):
            return None
        if state.get("last_price_event_key") == event_key:
            return None

        revision = self.profile_revision(symbol)
        generation = str(state.get("generation") or 1)
        notification_id = hashlib.sha256("|".join([
            self.uid,
            generation,
            symbol,
            revision,
            event_key,
        ]).encode()).hexdigest()
        if any(item.get("id") == notification_id for item in self.notifications):
            state["last_price_event_key"] = event_key
            return None

        event_snapshot = copy.deepcopy(event)
        event_snapshot.update({
            "event_type": "PRICE_EXIT",
            "event_direction": "BEARISH",
            "observed_at": event.get("observed_at") or datetime.now(timezone.utc).isoformat(),
            "price_event_key": event_key,
        })
        # Close the virtual long lifecycle before checking delivery settings.
        # A disabled channel must not leave the virtual position active.
        state.update({
            "profile_revision": revision,
            "last_actionable_direction": "BEARISH",
            "last_direction": "BEARISH",
            "virtual_position": False,
            "virtual_entry_price": None,
            "virtual_entry_atr": None,
            "virtual_entry_candle_key": None,
            "virtual_exit_mode": None,
            "virtual_exit_settings": {},
            "virtual_exit_levels": {},
            "virtual_peak_price": None,
            "virtual_trailing_stop": None,
            "last_price_event_key": event_key,
            "last_price_event": event_snapshot,
            "last_price_event_at": now_ts,
            "last_exit_event": event_snapshot,
            "last_exit_event_candle_key": state.get("last_candle_key"),
            "last_status": "price_event",
        })

        direction_enabled = bool(profile.get(
            "alert_bearish_enabled",
            self.notification_settings.get("bearish_enabled", True),
        ))
        raw_cooldown = profile.get(
            "alert_cooldown_minutes",
            self.notification_settings.get("cooldown_minutes", 60),
        )
        try:
            cooldown_minutes = max(0, int(raw_cooldown)) if raw_cooldown is not None else 60
        except (TypeError, ValueError):
            cooldown_minutes = 60
        last_alert_at = float(state.get("last_bearish_alert_at") or 0)
        cooldown_active = last_alert_at > 0 and (now_ts - last_alert_at) < cooldown_minutes * 60
        browser_enabled = bool(self.notification_settings.get("browser_enabled", True))
        email_enabled = bool(profile.get("email_enabled", self.notification_settings.get("email_enabled")))
        sms_enabled = bool(self.notification_settings.get("sms_enabled"))
        sms_phone_number = normalize_screener_sms_phone(self.notification_settings.get("sms_phone_number"))
        if not direction_enabled or cooldown_active or (not browser_enabled and not email_enabled and not sms_enabled):
            return None

        level_label = str(event.get("level_name") or "level").strip().title()
        mode_label = str(event.get("strategy_label") or event.get("mode") or "Price").strip()
        candle_key = str(state.get("last_candle_key") or "")
        notification = {
            "id": notification_id,
            "uid": self.uid,
            "symbol": symbol,
            "direction": "BEARISH",
            "event_direction": "BEARISH",
            "event_type": "PRICE_EXIT",
            "alert_kind": "PRICE_EVENT",
            "title": f"{symbol} {level_label} reached",
            "created_at": now_ts,
            "candle_key": candle_key,
            "candle_open_ts": state.get("last_candle_open_ts"),
            "candle_close_ts": state.get("last_candle_close_ts"),
            "profile_revision": revision,
            "settings_snapshot": copy.deepcopy(profile),
            "generation": generation,
            "price": trigger_price,
            "reason": event.get("text") or f"{mode_label} {level_label.lower()} reached by live price.",
            "signals": {},
            "sentiment": {},
            "atr": event.get("atr"),
            "atr_levels": event.get("atr_levels") or {},
            "price_event": event_snapshot,
            "read": False,
            "browser_status": "delivered" if browser_enabled else "disabled",
            "email_status": "queued" if email_enabled else "not_requested",
            "sms_status": "queued" if sms_enabled and sms_phone_number else ("setup_required" if sms_enabled else "not_requested"),
        }
        self.notifications.insert(0, notification)
        self.notifications = self.notifications[:1000]
        state["last_bearish_alert_at"] = now_ts
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
            f"[screener] Price alert created: {symbol} {level_label} "
            f"price={trigger_price} event_key={event_key} created_at={now_ts}"
        )
        return notification


    def commit_schedule_event(self, symbol: str, event: dict, now_ts: float | None = None) -> dict | None:
        """Commit one Session Rider schedule alert (buy time / sell time reached).

        Clock events, not candle signals: each (session, BUY|SELL) key alerts once. They follow
        the profile's schedule-alert switch, not the bullish/bearish filters (a scheduled sell is
        an exit, not a bearish signal). Analysis only; nothing is ordered."""
        now_ts = now_ts or time.time()
        symbol = normalize_screener_symbol(symbol)
        key = str((event or {}).get("key") or "").strip()
        kind = str((event or {}).get("event") or "").upper()
        if not symbol or not key or kind not in {"BUY", "SELL"}:
            return None
        profile = self.profiles.get(symbol, {})
        state = self.evaluation_state.setdefault(symbol, {})
        seen = [k for k in (state.get("schedule_event_keys") or []) if isinstance(k, str)]
        if key in seen:
            return None
        state["schedule_event_keys"] = (seen + [key])[-20:]
        state["last_schedule_event"] = {"key": key, "event": kind, "at": now_ts}
        if not bool(profile.get("session_alerts_enabled", True)):
            return None
        revision = self.profile_revision(symbol)
        notification_id = hashlib.sha256("|".join([self.uid, symbol, revision, "schedule", key]).encode()).hexdigest()
        if any(item.get("id") == notification_id for item in self.notifications):
            return None
        browser_enabled = bool(self.notification_settings.get("browser_enabled", True))
        email_enabled = bool(profile.get("email_enabled", self.notification_settings.get("email_enabled")))
        sms_enabled = bool(self.notification_settings.get("sms_enabled"))
        sms_phone_number = normalize_screener_sms_phone(self.notification_settings.get("sms_phone_number"))
        if not browser_enabled and not email_enabled and not sms_enabled:
            return None
        verb = "Buy" if kind == "BUY" else "Sell"
        notification = {
            "id": notification_id,
            "uid": self.uid,
            "symbol": symbol,
            "direction": "BULLISH" if kind == "BUY" else "BEARISH",
            "event_direction": "BULLISH" if kind == "BUY" else "BEARISH",
            "event_type": "SCHEDULE",
            "alert_kind": "SCHEDULE",
            "title": f"{symbol} Session Rider: {verb} time",
            "created_at": now_ts,
            "candle_key": "",
            "profile_revision": revision,
            "settings_snapshot": copy.deepcopy(profile),
            "generation": str(state.get("generation") or 1),
            "price": event.get("price"),
            "reason": str(event.get("text") or f"Scheduled {verb.lower()} time reached."),
            "schedule_event": {k: v for k, v in event.items() if k in ("event", "key", "at_label", "mode", "entry_session")},
            "signals": {},
            "sentiment": {},
            "atr_levels": {},
            "read": False,
            "browser_status": "delivered" if browser_enabled else "disabled",
            "email_status": "queued" if email_enabled else "not_requested",
            "sms_status": "queued" if sms_enabled and sms_phone_number else ("setup_required" if sms_enabled else "not_requested"),
        }
        self.notifications.insert(0, notification)
        self.notifications = self.notifications[:1000]
        if notification["email_status"] == "queued":
            self.email_outbox.append({"id": notification_id, "notification_id": notification_id, "status": "queued",
                                      "attempts": 0, "next_attempt_at": now_ts, "created_at": now_ts})
            self.email_outbox = self.email_outbox[-1000:]
        if notification["sms_status"] == "queued":
            self.sms_outbox.append({"id": notification_id, "notification_id": notification_id, "to": sms_phone_number,
                                    "status": "queued", "attempts": 0, "next_attempt_at": now_ts, "created_at": now_ts})
            self.sms_outbox = self.sms_outbox[-1000:]
        print(f"[screener] Schedule alert created: {symbol} {kind} key={key}")
        return notification


def candle_key_regressed(previous_key: Any, current_key: Any) -> bool:
    """True when ``current_key`` is an older completed candle than ``previous_key`` of the SAME timeframe.

    Keys look like ``30MIN:1760000000``. Timestamps of different timeframes (a timeframe change, or a
    Strategy that scans daily bars after intraday ones) are not comparable, so they never count as a
    regression; the baseline reset on a settings save already starts a new candle ledger for them.
    """
    def parts(key: Any):
        text = str(key or "")
        timeframe, _, stamp = text.rpartition(":")
        return timeframe.strip().upper(), _safe_float(stamp, 0.0)

    previous_tf, previous_ts = parts(previous_key)
    current_tf, current_ts = parts(current_key)
    if previous_ts <= 0 or current_ts <= 0:
        return False
    if previous_tf != current_tf:
        return False
    return current_ts < previous_ts


def profile_revision(settings: dict) -> str:
    ignored = {
        "revision", "profile_revision", "started_at", "enabled", "paused",
        # Scan cadence changes scheduling only; they do not change the
        # technical inputs or invalidate the completed-candle baseline.
        "scan_interval_seconds",
        "price_watch_interval_seconds",
        "alert_preferences", "alert_bullish_enabled", "alert_bearish_enabled",
        "alert_cooldown_minutes", "news_watch_enabled", "earnings_watch_enabled", "fundamental_watch_enabled",
        "session_alerts_enabled",
        "symbol", "ticker",
        # alert_source is derived from strategy_mode + the preset flags, which
        # are hashed already. The migration markers are display-only.
        "alert_source", "legacy_strategy_mode", "migration_notice",
    }
    clean = {
        key: value for key, value in (settings or {}).items()
        if not str(key).startswith("_") and key not in ignored
    }
    if str((settings or {}).get("alert_source") or "").lower() == "none":
        # Pausing alerts must start a fresh baseline on resume.
        clean["alert_source_paused"] = True
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()[:16]


# Exits that are decided by price levels alone. Like the Bots engine ("Sell Signal Ignored"), an indicator SELL does not
# end a setup under these exits; the exit fires when its levels are reached. Hybrid exits (…_atr/_pct/_pivot/…) and the
# plain indicator exit still use the bearish indicator confluence.
PURE_STRUCTURAL_EXIT_MODES = frozenset({
    "sltp", "atr", "percentage", "custom_sltp", "custom_underlying_sltp",
    "pivot_points", "high_low", "swing_points",
})


def indicator_sell_is_ignored(sell_mode: str) -> bool:
    return str(sell_mode or "").strip().lower() in PURE_STRUCTURAL_EXIT_MODES


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
        preset_info = result.get("strategy_preset")
        if isinstance(preset_info, dict):
            # A strategy preset publishes its STATE (long regime = BULLISH,
            # flat after an exit = BEARISH) so one missed scan cannot lose an
            # exit alert. The Screener has no position, so ``action`` is not used.
            result["direction"] = str(preset_info.get("alert_direction") or "NEUTRAL").upper()
        else:
            result["direction"] = {"BUY": "BULLISH", "SELL": "BEARISH"}.get(result.get("action"), "NEUTRAL")
        result["profile_revision"] = profile_revision(snapshot)
        atr = float(result.get("atr") or 0)
        price = float(result.get("price_raw") or 0)
        buy_mode = str(snapshot.get("buy_mode") or "indicator").strip().lower()
        sell_mode = str(snapshot.get("sell_mode") or snapshot.get("options_exit_strategy") or "indicator").strip().lower()
        if (
            not isinstance(preset_info, dict)
            and result["direction"] == "BEARISH"
            and indicator_sell_is_ignored(sell_mode)
        ):
            # The exit is a price-level exit: bearish indicators are not the Sell trigger, so they must not send a Sell
            # alert. The card's Sell evidence shows the exit levels; the exit alert comes from the level watcher once a
            # Buy alert has opened the (analysis-only) virtual position.
            ignored_reason = str(result.get("reason") or "").strip()
            result["indicator_sell_ignored"] = True
            result["direction"] = "NEUTRAL"
            result["action"] = "HOLD"
            result["reason"] = (
                f"Sell signal ignored: the exit is {sell_mode.replace('_', ' ')} (price levels), not indicators."
                + (f" | {ignored_reason}" if ignored_reason else "")
            )
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

    MAX_STATES = 1024
    STATE_IDLE_SECONDS = 3600.0

    def __init__(self, db):
        self.db = db
        self.states: dict[str, ScreenerUserState] = {}
        # Track states that came from the active-user registry separately from
        # states created by a live API request. This lets registry refreshes
        # drop users that were deactivated without evicting a user who is
        # currently opening the Screener while the registry is unavailable.
        self._registry_uids: set[str] = set()
        self._state_last_seen: dict[str, float] = {}
        # Firestore is only the discovery source. Keep the last successful
        # registry locally so a transient cloud/network outage cannot make the
        # independent screener scheduler lose its users.
        self._registry_cache_namespace = "screener_registry"
        self._registry_last_warning_at = 0.0
        # One Firestore registry read at a time. asyncio.wait_for cannot stop a worker thread, so a read that
        # outlives its timeout is reused by the next refresh instead of piling up another query behind it.
        self._registry_read_future = None
        self._registry_failures = 0
        self._registry_retry_at = 0.0
        self.evaluation_semaphore = asyncio.Semaphore(4)
        # The completed-candle probe is a tiny request. It gets its own slots so it never queues behind 45 s full
        # evaluations (that queueing was the cause of intermittent "probe unavailable" warnings).
        self.probe_semaphore = asyncio.Semaphore(4)
        self.work_tracker = ScreenerWorkTracker()
        self.news_semaphore = asyncio.Semaphore(3)
        self.earnings_semaphore = asyncio.Semaphore(3)

    def get_state(self, uid: str) -> ScreenerUserState:
        uid = str(uid or '').strip()
        if not uid:
            raise ValueError("Screener user id is required")
        now = time.monotonic()
        if uid not in self.states and len(self.states) >= self.MAX_STATES:
            stale = sorted(
                (seen, stale_uid) for stale_uid, seen in self._state_last_seen.items()
                if now - seen > self.STATE_IDLE_SECONDS
            )
            for _, stale_uid in stale:
                self.states.pop(stale_uid, None)
                self._state_last_seen.pop(stale_uid, None)
                self._registry_uids.discard(stale_uid)
                if len(self.states) < self.MAX_STATES:
                    break
        if uid not in self.states and len(self.states) >= self.MAX_STATES:
            raise RuntimeError("Screener state capacity is temporarily full; retry shortly.")
        # Keep an authenticated request available as an in-memory fallback;
        # scheduled discovery uses the active Firestore registry when it is
        # reachable and never relies on this marker as durable authority.
        self._registry_uids.add(uid)
        if uid not in self.states:
            self.states[uid] = ScreenerUserState(uid, self.db)
        self._state_last_seen[uid] = now
        return self.states[uid]

    async def discover_active_users(self) -> list[ScreenerUserState]:
        now = time.monotonic()

        async def _states_for(uids: set[str]) -> list[ScreenerUserState]:
            states = [self.get_state(uid) for uid in sorted(uids)]
            await asyncio.gather(*(state.load() for state in states), return_exceptions=True)
            return states

        async def _load_local_uids() -> tuple[set[str], set[str], bool]:
            try:
                from local_store import list_state_users, load_state

                registry_task = asyncio.to_thread(
                    load_state, "__screener__", self._registry_cache_namespace
                )
                state_users_task = asyncio.to_thread(list_state_users, "screener_state")
                payload, state_users = await asyncio.gather(registry_task, state_users_task)
                cache_present = isinstance(payload, dict) and "uids" in payload
                payload = payload or {}
                values = payload.get("uids", []) if isinstance(payload, dict) else []
                cached_uids = {str(uid).strip() for uid in values if str(uid).strip()}
                stored_uids = {
                    str(uid).strip() for uid in (state_users or []) if str(uid).strip()
                }
                return cached_uids, stored_uids, cache_present
            except Exception as exc:
                # The in-memory registry remains a valid fallback if local
                # persistence is unavailable as well.
                if now - self._registry_last_warning_at >= 60:
                    self._registry_last_warning_at = now
                    print(f"[ScreenerManager] Local registry unavailable; using memory: {exc}")
                return set(), set(), False

        cached_uids, stored_uids, cache_present = await _load_local_uids()

        async def _save_registry_cache(uids: set[str]) -> None:
            try:
                from local_store import save_state

                await asyncio.to_thread(
                    save_state,
                    "__screener__",
                    self._registry_cache_namespace,
                    {"uids": sorted(uids), "saved_at": time.time()},
                )
            except Exception as exc:
                if now - self._registry_last_warning_at >= 60:
                    self._registry_last_warning_at = now
                    print(f"[ScreenerManager] Local registry cache save unavailable: {exc}")

        async def _apply_registry(uids: set[str], prune: bool = True) -> list[ScreenerUserState]:
            active_uids = {str(uid).strip() for uid in uids if str(uid).strip()}
            if prune:
                for stale_uid in self._registry_uids - active_uids:
                    self.states.pop(stale_uid, None)
            self._registry_uids = active_uids
            await _save_registry_cache(active_uids)
            return await _states_for(active_uids)

        # Firestore is authoritative when available, so deactivated users are
        # pruned. Local SQLite/cache data remains the outage fallback.
        if self.db is not None:
            def _read_registry():
                from google.cloud.firestore_v1.base_query import FieldFilter
                query = self.db.collection("screener_users").where(
                    filter=FieldFilter("active", "==", True)
                )
                return list(query.stream(timeout=3.0, retry=None))

            if now >= self._registry_retry_at:
                try:
                    future = self._registry_read_future
                    if future is None or future.done():
                        future = asyncio.ensure_future(asyncio.to_thread(_read_registry))
                        self._registry_read_future = future
                    # The read has its own 3 s network timeout; this outer limit only covers a busy worker pool.
                    documents = await asyncio.wait_for(asyncio.shield(future), timeout=10.0)
                    self._registry_failures = 0
                    return await _apply_registry({document.id for document in documents})
                except Exception as exc:
                    self._registry_failures += 1
                    # Bounded exponential backoff with jitter (30 s .. 5 min) so a struggling Firestore is not hammered.
                    import random

                    delay = min(300.0, 30.0 * (2 ** min(self._registry_failures - 1, 3))) * (0.8 + 0.4 * random.random())
                    self._registry_retry_at = now + delay
                    if now - self._registry_last_warning_at >= 60:
                        self._registry_last_warning_at = now
                        detail = str(exc) or "read exceeded 10s while the worker pool was busy"
                        print(
                            f"[ScreenerManager] Registry unavailable ({type(exc).__name__}: {detail}); "
                            f"using local cache, retrying in {delay:.0f}s"
                        )

        # Prefer the last explicit registry cache. If it does not exist yet,
        # discover persisted state files and authenticated in-memory users.
        if cache_present:
            return await _apply_registry(cached_uids, prune=False)
        if stored_uids:
            return await _apply_registry(stored_uids, prune=False)
        return await _apply_registry(self._registry_uids, prune=False)
