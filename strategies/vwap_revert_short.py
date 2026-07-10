"""Fade overextension above the daily VWAP — works when there is no bull trend."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class VwapRevertShort(Strategy):
    meta = {
        "name": "vwap_revert_short",
        "version": 2,
        "description": "Short 5m stretch >1.5 ATR above day-VWAP with RSI>68, target VWAP",
        # v2 2026-07-10: dropped large_alts on live evidence — mid_alts PF 1.50
        # (n=88, +2796) vs large_alts PF 0.68 (n=52, -1301). Live-paper split is
        # forward data on both groups, stronger than any backtest re-check.
        "groups": ["mid_alts"],
        "regimes": ["SIDE", "BEAR"],
        "status": "active",
        "params": {
            "stretch_atr": 1.5,
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
        if a <= 0 or px - vwap < p["stretch_atr"] * a:
            return None
        r = float(ind.rsi(df["c"], p["rsi_period"]).iloc[-1])
        if r < p["rsi_min"]:
            return None
        return Signal("short", sl=px + p["sl_atr"] * a, tp=vwap,
                      reason=f"{(px - vwap) / a:.1f} ATR above VWAP, RSI {r:.0f}")


STRATEGY = VwapRevertShort()
