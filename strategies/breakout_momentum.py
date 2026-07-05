"""Volume-confirmed range breakout for fast movers (mid caps + memecoins)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class BreakoutMomentum(Strategy):
    meta = {
        "name": "breakout_momentum",
        "version": 1,
        "description": "5m close above 8h high with volume >2.5x average; funding-spike veto",
        "groups": ["mid_alts", "memes"],
        "regimes": ["BULL", "SIDE"],
        "status": "active",
        "params": {
            "lookback_bars": 96,          # 8h of 5m bars
            "vol_mult": 2.5,
            "funding_veto": 0.0005,       # skip if |funding| > 0.05% (crowded)
            "atr_period": 14, "sl_atr": 1.5, "tp_atr": 3.0,
            "trail_ema": 20,
        },
    }

    def should_enter(self, ctx):
        p = self.meta["params"]
        if abs(ctx.funding) > p["funding_veto"]:
            return None
        df = ctx.tf("5min")
        n = p["lookback_bars"]
        if len(df) < n + 5:
            return None
        prior_high = float(df["h"].iloc[-n - 1:-1].max())
        last = df.iloc[-1]
        if float(last["c"]) <= prior_high:
            return None
        avg_vol = float(df["v"].iloc[-n - 1:-1].mean())
        if avg_vol <= 0 or float(last["v"]) < p["vol_mult"] * avg_vol:
            return None
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        px = ctx.price
        return Signal("long", sl=px - p["sl_atr"] * a, tp=px + p["tp_atr"] * a,
                      reason=f"8h breakout, vol x{float(last['v']) / avg_vol:.1f}")

    def should_exit(self, ctx, pos):
        p = self.meta["params"]
        df = ctx.tf("5min")
        if len(df) < p["trail_ema"]:
            return None
        if df["c"].iloc[-1] < ind.ema(df["c"], p["trail_ema"]).iloc[-1]:
            return "momentum faded (EMA20 5m)"
        return None


STRATEGY = BreakoutMomentum()
