"""BULL test of the VWAP-fade mechanic: does fading greed spikes work in an uptrend?

Frozen clone of vwap_revert_short v2 (identical params) with regimes=['BULL'].
Because the rule was tuned entirely on SIDE/BEAR, every BULL bar here is
out-of-sample by construction — nothing is fit to this window. Pre-registered
gate: n>=20 BULL trades AND PF>=1.15 to earn live incubation; else retire.
Created paused (pre-gate, does not trade) for backtest only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class VwapRevertShortBull(Strategy):
    meta = {
        "name": "vwap_revert_short_bull",
        "version": 1,
        "description": "BULL test: short 5m stretch >1.5 ATR above day-VWAP with RSI>68, target VWAP",
        "groups": ["mid_alts"],
        "regimes": ["BULL"],
        "status": "paused",
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


STRATEGY = VwapRevertShortBull()
