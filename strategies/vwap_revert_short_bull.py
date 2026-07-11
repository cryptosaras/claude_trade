"""BULL test of the VWAP-fade mechanic: does fading greed spikes work in an uptrend?

RESULT (2026-07-11): FAILED, RETIRED without ever trading. Frozen clone of
vwap_revert_short v2 (identical params) with regimes=['BULL']. Backtest BULL-only:
21d n=186 PF 0.76 (-3046, -1.45%/day); 30d n=302 PF 0.83 (-2955, -0.99%/day).
Both fail the pre-registered gate (n>=20 AND PF>=1.15). The SAME mechanic is
PF 1.50 live in SIDE on mid_alts — regime is the entire edge. Fading a
>1.5 ATR / RSI>68 stretch in a bull grind-up just shorts strength that keeps
grinding (win rate 24-28%): the short-side mirror of the vwap_pullback_long
knife-catch. The VWAP-fade edge is a chop phenomenon, not a trend one.
Do not retry BULL fade without a materially different rule.
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
        "status": "retired",
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
