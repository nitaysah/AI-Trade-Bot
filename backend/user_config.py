import config as global_config
import contextvars

active_evaluation_context = contextvars.ContextVar('active_evaluation_context', default='dashboard')
active_ticker_context = contextvars.ContextVar('active_ticker_context', default=None)
active_draft_ticker_settings = contextvars.ContextVar('active_draft_ticker_settings', default=None)

class UserConfig:
    def __init__(self, uid: str):
        self.uid = uid
        self.WATCHLIST = []
        self.TRADELIST = []
        self.DEFAULT_TIMEFRAME = global_config.DEFAULT_TIMEFRAME
        self._ticker_settings = {}
        self.TICKER_AMOUNTS = {}
        self.ALPACA_API_KEY = ""
        self.ALPACA_SECRET_KEY = ""
        self.ALPACA_PAPER = True
        self.TIMEZONE = getattr(global_config, 'TIMEZONE', 'US/Central')
        # Versioned legal acceptance is persisted per user and enforced by the API.
        self.legal_acceptance = {}
        # Dashboard-only regular indicator workspace. The backend owns the
        # authoritative displayed order, enabled subset, and favorites.
        self.dashboard_indicator_workspace = {}
        # Account-level live-bot risk state. This is persisted separately from
        # per-ticker pause settings so protective exits can keep running.
        self.global_risk_state = {}
        self.SCAN_INTERVAL_SECONDS = getattr(global_config, 'SCAN_INTERVAL_SECONDS', 60)
        self.OPTIONS_SCAN_INTERVAL_SECONDS = getattr(global_config, 'OPTIONS_SCAN_INTERVAL_SECONDS', 10)
        
        # Load all ENABLE_ toggles and indicator parameters from global config as defaults
        self.toggles = {k: getattr(global_config, k) for k in dir(global_config) if k.startswith("ENABLE_")}
        self.parameters = {k: getattr(global_config, k) for k in dir(global_config) if k.isupper() and not k.startswith("_") and not isinstance(getattr(global_config, k), (list, dict)) and k not in ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "GROQ_API_KEY", "FERNET_KEY"]}
        
        # Isolated context-specific settings
        self.contexts = {
            "dashboard": { "toggles": {}, "parameters": {} },
            "bots": { "toggles": {}, "parameters": {} },
            "backtest": { "toggles": {}, "parameters": {} }
        }

    @property
    def TICKER_SETTINGS(self):
        overlay = active_draft_ticker_settings.get()
        if not overlay:
            return self._ticker_settings

        merged = {}
        for key, value in self._ticker_settings.items():
            merged[key] = value.copy() if isinstance(value, dict) else value

        for key, value in overlay.items():
            ticker_key = str(key or "").upper().strip()
            if not ticker_key:
                continue
            base_value = merged.get(ticker_key, {})
            if isinstance(base_value, dict) and isinstance(value, dict):
                merged[ticker_key] = {**base_value, **value}
            else:
                merged[ticker_key] = value
        return merged

    @TICKER_SETTINGS.setter
    def TICKER_SETTINGS(self, value):
        normalized = {}
        if isinstance(value, dict):
            for key, settings in value.items():
                ticker_key = str(key or "").upper().strip()
                if not ticker_key:
                    continue
                normalized[ticker_key] = settings.copy() if isinstance(settings, dict) else settings
        self._ticker_settings = normalized
        
    @property
    def active_toggles(self):
        ctx = active_evaluation_context.get()
        merged = self.toggles.copy()
        contexts_dict = self.__dict__.get('contexts', {})
        
        if ctx == "backtest":
            if ctx in contexts_dict:
                merged.update(contexts_dict[ctx].get('toggles', {}))
            return merged
            
        ticker = active_ticker_context.get()
        if ticker and ticker in self.TICKER_SETTINGS:
            # For live bots, we might not have a separate toggle override map per ticker, but just in case
            pass
            
        if ctx in contexts_dict:
            merged.update(contexts_dict[ctx].get('toggles', {}))
        return merged

    @property
    def active_parameters(self):
        ctx = active_evaluation_context.get()
        merged = self.parameters.copy()
        contexts_dict = self.__dict__.get('contexts', {})
        
        if ctx == "backtest":
            if ctx in contexts_dict:
                merged.update(contexts_dict[ctx].get('parameters', {}))
            return merged
            
        ticker = active_ticker_context.get()
        if ctx in contexts_dict:
            merged.update(contexts_dict[ctx].get('parameters', {}))
            
        if ticker and ticker in self.TICKER_SETTINGS:
            overrides = self.TICKER_SETTINGS[ticker].get('indicator_overrides', {})
            merged.update(overrides)
            
        return merged

    def _coerce_config_value(self, name, value):
        try:
            default_val = getattr(global_config, name, None)
            if isinstance(default_val, bool):
                return bool(value)
            if isinstance(default_val, int):
                return int(float(value))
            if isinstance(default_val, float):
                return float(value)
        except Exception:
            pass
        return value

    def __getattr__(self, name):
        """Allow dot-notation access to configs, falling back to ticker-specific, then context, then global, then default."""
        ctx = active_evaluation_context.get()
        contexts_dict = self.__dict__.get('contexts', {})
        
        if ctx == "backtest":
            if ctx in contexts_dict:
                ctx_data = contexts_dict[ctx]
                if name in ctx_data.get('parameters', {}): return self._coerce_config_value(name, ctx_data['parameters'][name])
                if name in ctx_data.get('toggles', {}): return self._coerce_config_value(name, ctx_data['toggles'][name])
        else:
            ticker = active_ticker_context.get()
            if ticker and ticker in self.TICKER_SETTINGS:
                overrides = self.TICKER_SETTINGS[ticker].get('indicator_overrides', {})
                if name in overrides:
                    return self._coerce_config_value(name, overrides[name])
            
            if ctx in contexts_dict:
                ctx_data = contexts_dict[ctx]
                if name in ctx_data.get('parameters', {}): return self._coerce_config_value(name, ctx_data['parameters'][name])
                if name in ctx_data.get('toggles', {}): return self._coerce_config_value(name, ctx_data['toggles'][name])

        if name in self.__dict__:
            return self.__dict__[name]
        if name in self.parameters:
            return self._coerce_config_value(name, self.parameters[name])
        if name in self.toggles:
            return self._coerce_config_value(name, self.toggles[name])
        try:
            return getattr(global_config, name)
        except AttributeError:
            raise AttributeError(f"'UserConfig' object has no attribute '{name}'")

    def get(self, key, default=None):
        """Dict-like safe retrieval of config values."""
        try:
            return getattr(self, key)
        except AttributeError:
            return default

    def set(self, key, value):
        if isinstance(value, str) and value.strip() != "":
            val_str = value.strip()
            try:
                if "." in val_str:
                    value = float(val_str)
                else:
                    value = int(val_str)
            except ValueError:
                pass
        ctx = active_evaluation_context.get()
        contexts_dict = self.__dict__.get('contexts', {})
        if ctx in contexts_dict:
            ctx_data = contexts_dict[ctx]
            if value == "" or value is None:
                # Revert to default by deleting override
                if key.startswith("ENABLE_"):
                    ctx_data.get('toggles', {}).pop(key, None)
                else:
                    ctx_data.get('parameters', {}).pop(key, None)
            else:
                if key.startswith("ENABLE_"):
                    ctx_data['toggles'][key] = value
                else:
                    ctx_data['parameters'][key] = value
        else:
            if value == "" or value is None:
                if key.startswith("ENABLE_"):
                    self.toggles.pop(key, None)
                else:
                    self.parameters.pop(key, None)
            else:
                if key.startswith("ENABLE_"):
                    self.toggles[key] = value
                else:
                    self.parameters[key] = value

_active_user_config = contextvars.ContextVar('active_user_config', default=None)
_default_user_config = None

def set_draft_ticker_settings(settings_map: dict):
    normalized = {}
    if isinstance(settings_map, dict):
        for key, value in settings_map.items():
            ticker_key = str(key or "").upper().strip()
            if not ticker_key:
                continue
            normalized[ticker_key] = value.copy() if isinstance(value, dict) else value
    return active_draft_ticker_settings.set(normalized or None)

def reset_draft_ticker_settings(token):
    active_draft_ticker_settings.reset(token)

def set_user_config(uc: UserConfig):
    return _active_user_config.set(uc)

def get_user_config() -> UserConfig:
    global _default_user_config
    uc = _active_user_config.get()
    if uc is None:
        # Fallback to a default one if running outside a user context (e.g. tests or startup initialization)
        if _default_user_config is None:
            _default_user_config = UserConfig("default")
        return _default_user_config
    return uc
