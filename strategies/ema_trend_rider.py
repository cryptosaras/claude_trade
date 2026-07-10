"""Bull-regime pullback entry on the 15m trend for slower, liquid coins."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class EmaTrendRider(Strategy):
    meta = {
        "name": "ema_trend_rider",
        "version": 1,
        "description": "15m uptrend (EMA50>EMA200), enter long on RSI pullback turning up",
        "groups": ["majors", "large_alts"],
        "regimes": ["BULL"],
        # retired 2026-07-10: PF 0.40, n=39, wr 18%, net -1554 in its intended
        # BULL regime (retire rule: PF<0.9 after 30+). Re-entered immediately
        # after every SL in chop; trend-following keeps failing on this tape.
        "status": "retired",
        "params": {
            "ema_fast": 50, "ema_slow": 200,
            "rsi_period": 14, "rsi_pullback": 45,
            "atr_period": 14, "sl_atr": 1.3, "tp_atr": 2.6,
        },
    }

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = ctx.tf("15min")
        if len(df) < p["ema_slow"] + 5:
            return None
        c = df["c"]
        ef, es = ind.ema(c, p["ema_fast"]), ind.ema(c, p["ema_slow"])
        if not (c.iloc[-1] > ef.iloc[-1] > es.iloc[-1]):
            return None
        r = ind.rsi(c, p["rsi_period"])
        # pullback: RSI dipped below threshold on the previous bar and is turning up
        if not (r.iloc[-2] < p["rsi_pullback"] and r.iloc[-1] > r.iloc[-2]):
            return None
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        px = ctx.price
        return Signal("long", sl=px - p["sl_atr"] * a, tp=px + p["tp_atr"] * a,
                      reason="15m trend pullback")

    def should_exit(self, ctx, pos):
        p = self.meta["params"]
        df = ctx.tf("15min")
        if len(df) < p["ema_fast"]:
            return None
        if df["c"].iloc[-1] < ind.ema(df["c"], p["ema_fast"]).iloc[-1] * 0.998:
            return "trend broke EMA50"
        return None


STRATEGY = EmaTrendRider()
