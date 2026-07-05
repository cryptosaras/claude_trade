"""Short-squeeze continuation (long-only). When funding is extremely negative
(shorts crowded, paying to stay in) while price is already ABOVE the 1h
EMA200, the crowd is trapped on the wrong side of an uptrend and its covering
fuels continuation. This RIDES the squeeze — the opposite of the retired
funding_skew_fade, whose event study refuted fading crowded positioning:
shorting positive-funding extremes lost at every threshold and horizon
(n=166 events, -1.7% net at 8h), while longing negative-funding extremes
above trend was the only surviving cell.

Episode-deduped event study (30d, 104 symbols, 8h debounce), funding <=
-0.03% & price > EMA200(1h), n=40 episodes: 12h net mean +3.5% / median
+0.55%, 24h median +1.6% hit 60%; drop-best-3 still positive at 8h+.
Below EMA200 the same funding LOSES (-6.0%/8h at extreme thr) — the trend
filter is the signal, not decoration. BEAR was negative at every threshold
(n=18/27/56) -> BULL/SIDE only. Fat right tail carries the mean: wide stop,
far TP, hold toward the 12h time-stop. Longs also RECEIVE the negative
funding while held. Edge is concentrated in small caps -> mid_alts+memes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class SqueezeRide(Strategy):
    meta = {
        "name": "squeeze_ride",
        "version": 2,
        "description": "Fresh flip to funding <= -0.03% with price above EMA200(1h) -> long the short squeeze",
        "groups": ["mid_alts", "memes"],
        # BEAR negative in the event study at every threshold — excluded
        "regimes": ["BULL", "SIDE"],
        "status": "paused",  # awaiting backtest gate — flip to incubating only on a pass
        "params": {
            "funding_extreme": -0.0003,  # entry: rate at or below this
            "funding_done": -0.00005,    # exit: crowd no longer paying to short
            "ema_period": 200,           # 1h trend filter — the load-bearing condition
            "atr_period": 14,
            "sl_atr": 2.5,               # wide: squeezes whip before they run
            "max_sl_pct": 0.05,
            "min_sl_pct": 0.01,
            "tp_pct": 0.10,              # fat right tail is the payoff; don't cap it early
        },
    }

    # v1 -> v2: enter only on an OBSERVED flip from non-extreme to extreme.
    # v1's 8h cooldown re-entered stale multi-day episodes the event study had
    # counted once (8 of 11 dev trades were the same RPL episode, PF 0.41);
    # the study measured the FIRST hours of an episode, so trade only those.
    # In-memory state: after a reload every symbol starts unknown -> no entry
    # until a fresh flip is witnessed. Conservative by construction.

    def __init__(self):
        self._was_extreme = {}  # symbol -> bool, last observed funding state

    def should_enter(self, ctx):
        p = self.meta["params"]
        extreme = ctx.funding <= p["funding_extreme"]
        prev = self._was_extreme.get(ctx.symbol)
        self._was_extreme[ctx.symbol] = extreme
        if not extreme or prev is not False:
            return None  # not extreme, still mid-episode, or state unknown
        df = ctx.tf("1h")
        if len(df) < p["ema_period"] + 5:
            return None
        e = float(ind.ema(df["c"], p["ema_period"]).iloc[-1])
        px = ctx.price
        if px <= e:
            return None
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        if a <= 0:
            return None
        sl = px - p["sl_atr"] * a
        dist = (px - sl) / px
        if not (p["min_sl_pct"] <= dist <= p["max_sl_pct"]):
            return None
        return Signal("long", sl=sl, tp=px * (1 + p["tp_pct"]),
                      reason=f"squeeze: funding {ctx.funding:.4%}, above 1h EMA200")

    def should_exit(self, ctx, pos):
        # fuel gone: shorts stopped paying — the continuation thesis is spent
        if ctx.funding >= self.meta["params"]["funding_done"]:
            return "funding-normalized"
        return None


STRATEGY = SqueezeRide()
