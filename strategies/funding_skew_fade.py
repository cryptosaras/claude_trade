"""Fade crowded positioning: extreme funding + price on the wrong side of the
1h EMA200 tends to resolve against the crowd.

RETIRED 2026-07-06, first honest gate after funding history reached 30d.
Backtest: dev 21d PF 0.31 (n=8, -2.39%); held-out 7d ZERO trades (the
+-0.03% threshold barely fires on majors/large_alts). Event study (30d, 104
symbols) refuted the core premise on the short side: shorting positive-
funding extremes LOSES at every threshold and horizon (its exact condition,
rate>=+0.03% & below EMA: n=63, -1.9% net at 8h, -5.9% at 24h) — crowded
longs keep winning. The long cell (negative funding + above EMA) was the
only survivor and is superseded by squeeze_ride, which rides it with proper
mechanics. Do not revive the fade side without new data or mechanism."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class FundingSkewFade(Strategy):
    meta = {
        "name": "funding_skew_fade",
        "version": 2,
        "description": "Funding >=+0.03% & price<EMA200(1h) -> short; <=-0.03% & price>EMA200 -> long",
        "groups": ["majors", "large_alts"],
        "regimes": ["BEAR", "SIDE"],
        "status": "retired",  # failed the gate + event study refuted the fade, see docstring
        "params": {
            "funding_extreme": 0.0003,
            "ema_period": 200,
            "atr_period": 14, "sl_atr": 2.0, "tp_pct": 0.035,
        },
    }

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = ctx.tf("1h")
        if len(df) < p["ema_period"] + 5:
            return None
        e = float(ind.ema(df["c"], p["ema_period"]).iloc[-1])
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        px = ctx.price
        if ctx.funding >= p["funding_extreme"] and px < e:
            return Signal("short", sl=px + p["sl_atr"] * a, tp=px * (1 - p["tp_pct"]),
                          reason=f"crowded longs, funding {ctx.funding:.4%}")
        if ctx.funding <= -p["funding_extreme"] and px > e:
            return Signal("long", sl=px - p["sl_atr"] * a, tp=px * (1 + p["tp_pct"]),
                          reason=f"crowded shorts, funding {ctx.funding:.4%}")
        return None


STRATEGY = FundingSkewFade()
