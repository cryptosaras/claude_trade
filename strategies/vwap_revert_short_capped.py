"""RETIRED 2026-07-18 — premise refuted on live data. This variant's hypothesis
was that shorting parabolic runaways (high stretch) is what costs the strategy.
Bucketing 278 live vwap_revert_short trades by TRUE logged entry stretch shows
the OPPOSITE: stretch >= 4.5 ATR is the most profitable lane (PF ~1.5, the best
bucket), and the losers are LOW-stretch entries. An upper cap would remove the
edge. The actionable change was a higher FLOOR, not a cap — shipped as
vwap_revert_short v3 (stretch_atr 4.5). Do not re-run the cap sweep; kept only
as a record. See reports/2026-07-18.

Original: TEST VARIANT (paused) of vwap_revert_short: adds an UPPER stretch cap
so we stop shorting parabolic runaways. Cap read from env STRETCH_MAX."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class VwapRevertShortCapped(Strategy):
    meta = {
        "name": "vwap_revert_short_capped",
        "version": 1,
        "description": "vwap_revert_short + upper stretch cap (parabolic filter)",
        "groups": ["mid_alts"],
        "regimes": ["SIDE", "BEAR"],
        "status": "retired",
        "params": {
            "stretch_atr": 1.5,
            "stretch_atr_max": float(os.environ.get("STRETCH_MAX", "4.0")),
            "rsi_period": 14, "rsi_min": 68,
            "atr_period": 14, "sl_atr": 1.4,
        },
    }

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = ctx.tf("5min")
        if len(df) < 80:
            return None
        vwap = float(ind.day_vwap(df).iloc[-1])
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        px = ctx.price
        if a <= 0:
            return None
        stretch = (px - vwap) / a
        if stretch < p["stretch_atr"] or stretch > p["stretch_atr_max"]:
            return None
        r = float(ind.rsi(df["c"], p["rsi_period"]).iloc[-1])
        if r < p["rsi_min"]:
            return None
        return Signal("short", sl=px + p["sl_atr"] * a, tp=vwap,
                      reason=f"{stretch:.1f} ATR above VWAP, RSI {r:.0f}")


STRATEGY = VwapRevertShortCapped()
