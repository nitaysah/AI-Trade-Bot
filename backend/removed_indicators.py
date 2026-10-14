"""Indicators that no longer exist. Saved bots, Screener profiles and backtest requests may still list them; they are
dropped when read so the rest of the saved selection keeps running."""

REMOVED_INDICATOR_NAMES = frozenset({
    "BotBulls1", "BotBulls2", "BotBulls3", "Botbulls4 News AI", "BotBulls5",
})

_LOWER = {name.lower() for name in REMOVED_INDICATOR_NAMES} | {"botbulls4"}


def is_removed(name) -> bool:
    return str(name or "").strip().lower() in _LOWER


def strip_removed(names):
    """Return (kept, removed) for a list of indicator names. Anything that is not a list is returned unchanged."""
    if not isinstance(names, (list, tuple)):
        return names, []
    kept, removed = [], []
    for name in names:
        (removed if is_removed(name) else kept).append(name)
    return kept, removed
