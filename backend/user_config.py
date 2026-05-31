import config as global_config
import contextvars

active_evaluation_context = contextvars.ContextVar('active_evaluation_context', default='dashboard')

class UserConfig:
    def __init__(self, uid: str):
        self.uid = uid
        self.WATCHLIST = []
        self.TRADELIST = []
        self.DEFAULT_TIMEFRAME = global_config.DEFAULT_TIMEFRAME
        self.TICKER_SETTINGS = {}
        self.TICKER_AMOUNTS = {}
        self.ALPACA_API_KEY = ""
        self.ALPACA_SECRET_KEY = ""
        self.ALPACA_PAPER = True
        self.TIMEZONE = getattr(global_config, 'TIMEZONE', 'US/Central')
        self.SCAN_INTERVAL_SECONDS = getattr(global_config, 'SCAN_INTERVAL_SECONDS', 60)
        
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
    def active_toggles(self):
        ctx = active_evaluation_context.get()
        contexts_dict = self.__dict__.get('contexts', {})
        merged = self.toggles.copy()
        if ctx in contexts_dict:
            merged.update(contexts_dict[ctx].get('toggles', {}))
        return merged

    @property
    def active_parameters(self):
        ctx = active_evaluation_context.get()
        contexts_dict = self.__dict__.get('contexts', {})
        merged = self.parameters.copy()
        if ctx in contexts_dict:
            merged.update(contexts_dict[ctx].get('parameters', {}))
        return merged

    def __getattr__(self, name):
        """Allow dot-notation access to configs, falling back to context-specific parameters/toggles, then global, then default."""
        ctx = active_evaluation_context.get()
        contexts_dict = self.__dict__.get('contexts', {})
        if ctx in contexts_dict:
            ctx_data = contexts_dict[ctx]
            if name in ctx_data.get('parameters', {}):
                return ctx_data['parameters'][name]
            if name in ctx_data.get('toggles', {}):
                return ctx_data['toggles'][name]

        if name in self.__dict__:
            return self.__dict__[name]
        if name in self.parameters:
            return self.parameters[name]
        if name in self.toggles:
            return self.toggles[name]
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

def set_user_config(uc: UserConfig):
    return _active_user_config.set(uc)

def get_user_config() -> UserConfig:
    uc = _active_user_config.get()
    if uc is None:
        # Fallback to a default one if running outside a user context (e.g. tests or startup initialization)
        return UserConfig("default")
    return uc

