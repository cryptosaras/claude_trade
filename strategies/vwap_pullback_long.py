"""BULL mirror of vwap_revert_short: buy panic dips below the daily VWAP.

Rationale (2026-07-10): every trend-following BULL strategy died on this tape
(breakout_momentum PF 0.31, ema_trend_rider PF 0.40) while mean reversion is
the only thing making money (vwap_revert_short mid_alts PF 1.50 n=88,
range_fader large_alts PF 1.21 n=45). This tests the same VWAP-stretch
mechanism on the long side, only in BULL where dips get bought.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class VwapPullbackLong(Strategy):
    meta = {
        "name": "vwap_pullback_long",
        "version": 1,
        "description": "BULL: long 5m stretch >1.5 ATR below day-VWAP with RSI<32, target VWAP",
        "groups": ["mid_alts"],
        "regimes": ["BULL"],
        # FAILED the gate 2026-07-10, never traded live: 21d PF 0.75 (n=111,
        # -2270), 7d PF 0.69 (n=111, -2648). A >1.5-ATR dip below VWAP in BULL
        # is a breakdown, not a pullback — shorts fade greed, longs catch
        # knives. Dead; don't revive without a new mechanism.
        "status": "retired",
        "params": {
            "stretch_atr": 1.5,
            "rsi_period": 14, "rsi_max": 32,
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
        if a <= 0 or vwap - px < p["stretch_atr"] * a:
            return None
        r = float(ind.rsi(df["c"], p["rsi_period"]).iloc[-1])
        if r > p["rsi_max"]:
            return None
        return Signal("long", sl=px - p["sl_atr"] * a, tp=vwap,
                      reason=f"{(vwap - px) / a:.1f} ATR below VWAP, RSI {r:.0f}")


STRATEGY = VwapPullbackLong()
