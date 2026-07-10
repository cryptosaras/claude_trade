"""Sideways-regime mean reversion: fade Bollinger extremes with RSI confirmation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class RangeFader(Strategy):
    meta = {
        "name": "range_fader",
        "version": 2,
        "description": "15m Bollinger(20,2.2) band touch + RSI extreme, target mid-band",
        # v2 2026-07-10: dropped majors on live evidence — large_alts PF 1.21
        # (n=45, +654) vs majors PF 0.76 (n=21, -414). BTC/ETH ranges too clean;
        # the band-touch edge lives in sloppier large-alt books.
        "groups": ["large_alts"],
        "regimes": ["SIDE"],
        "status": "active",
        "params": {
            "bb_period": 20, "bb_k": 2.2,
            "rsi_period": 14, "rsi_low": 28, "rsi_high": 72,
            "atr_period": 14, "sl_atr": 1.2,
        },
    }

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = ctx.tf("15min")
        if len(df) < p["bb_period"] + 5:
            return None
        c = df["c"]
        lo, mid, hi = ind.bollinger(c, p["bb_period"], p["bb_k"])
        r = float(ind.rsi(c, p["rsi_period"]).iloc[-1])
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        px = ctx.price
        if px <= float(lo.iloc[-1]) and r < p["rsi_low"]:
            return Signal("long", sl=px - p["sl_atr"] * a, tp=float(mid.iloc[-1]),
                          reason=f"lower band, RSI {r:.0f}")
        if px >= float(hi.iloc[-1]) and r > p["rsi_high"]:
            return Signal("short", sl=px + p["sl_atr"] * a, tp=float(mid.iloc[-1]),
                          reason=f"upper band, RSI {r:.0f}")
        return None


STRATEGY = RangeFader()
