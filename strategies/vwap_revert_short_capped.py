"""TEST VARIANT (paused) of vwap_revert_short: adds an UPPER stretch cap so we
stop shorting parabolic runaways (APT/EIGEN at 7-10 ATR above VWAP that keep
ripping and take 4-6 consecutive stops). Cap is read from env STRETCH_MAX so a
backtest sweep can try values without re-pushing. Not for live trading — status
paused; --strategy forces it active only inside an explicit backtest."""
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
        "status": "paused",
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
