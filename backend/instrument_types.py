"""Small, provider-independent instrument classifications.

Yahoo's company-fundamentals endpoints do not consistently support ETFs and
can write noisy 404 responses to stderr.  Keep the known index/sector funds we
use throughout the application out of company-only enrichment paths.
"""

from __future__ import annotations


KNOWN_ETF_SYMBOLS = frozenset({
    "ARKK", "DIA", "EEM", "EFA", "GLD", "HYG", "IWM", "LQD", "QQQ",
    "SLV", "SPY", "TLT", "UNG", "USO", "VTI", "XLB", "XLC", "XLE",
    "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
})


def is_known_etf(symbol: str) -> bool:
    """Return whether *symbol* is an ETF known to the application's universe."""
    return str(symbol or "").strip().upper() in KNOWN_ETF_SYMBOLS
