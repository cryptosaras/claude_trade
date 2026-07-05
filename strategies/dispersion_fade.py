"""Basket-dispersion fade (short-only). Meme/mid-alt coins co-move on group
attention; when ONE coin's 1h return stretches >=2% above the mean 1h return
of its own group basket, the move is idiosyncratic (single-coin attention,
liquidation chase) and tends to bleed back toward the basket within 1-2h.
Event study, 30d 1m data, memes+mid_alts, first-cross events (2h debounce):
up-outlier fade profit n=115, +0.33% gross / +0.17% net at 60m, 65% hit,
building to +0.24% net at 120m; SIDE strongest, no negative regime.
Down-outliers showed no edge (n=37, net -0.04%, hit 46%) -> shorts only.
The cross-sectional condition is what separates this from the retired
pump_fade: a coin pumping WITH its basket is market beta, not an event.

RETIRED AT THE GATE 2026-07-05, never traded live. Held-out symbols 21d:
pooled PF 0.87 (n=20); mid_alts-only held-out PF 0.58 (n=24) vs dev 1.23
(n=41); every regime cell INVERTS across the symbol split (BEAR 1.72/0.55,
BULL 1.40/0.25, SIDE 0.85/1.62) — noise, not signal. The fixed-horizon
event edge (~+0.17% net at 60m) is too small to survive real trade
mechanics: avg hold 0.46h, fees ~= 100% of net on the best run. Frequent-
but-tiny edges can't be certified at n~50; rarer >=2.5% stretches give
n~7/21d. Kept as documentation of the negative result."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402


class DispersionFade(Strategy):
    meta = {
        "name": "dispersion_fade",
        "version": 2,
        "description": "Short a coin stretched >=2% above its group basket's 1h return",
        "groups": ["mid_alts", "memes"],
        # event study: shorts net-positive in all three regimes (SIDE strongest)
        "regimes": ["BULL", "BEAR", "SIDE"],
        "status": "retired",  # failed the held-out gate, see docstring — do not revive as-is
        "params": {
            "ret_bars": 60,         # return window: 60 x 1m = 1h
            "stretch_min": 0.02,    # coin 1h return minus basket mean, entry threshold
            "fresh_frac": 0.75,     # 15m ago stretch must have been < this x threshold
            "fresh_bars": 15,       # ... that many minutes ago (fresh cross, not stale stretch)
            "min_peers": 4,         # basket needs at least this many symbols with data
            "sl_frac": 0.6,         # SL this fraction of the stretch further against us
            "tp_frac": 0.5,         # TP at this fraction of the stretch retraced
            "min_sl_pct": 0.005,    # fee-drag guard
            "max_sl_pct": 0.03,     # skip ultra-parabolics (that was pump_fade, it died)
            "exit_stretch": 0.005,  # discretionary exit once reversion is done
            "max_hold_min": 150,    # edge is spent by ~2h; don't ride to the 12h stop
            "cooldown_min": 120,    # per-symbol re-entry cooldown (matches study debounce)
        },
    }

    def __init__(self):
        self._last_entry = {}  # symbol -> entry ts (in-memory; resets on reload, cheap)

    def _stretch(self, ctx, back: int = 0):
        """Coin 1h return minus basket mean 1h return, `back` minutes ago."""
        p = self.meta["params"]
        n = p["ret_bars"]
        rets, me = [], None
        for sym, df in ctx.peers().items():
            if len(df) < n + back + 1:
                continue
            c1 = float(df["c"].iloc[-1 - back])
            c0 = float(df["c"].iloc[-1 - back - n])
            if c0 <= 0:
                continue
            r = c1 / c0 - 1
            rets.append(r)
            if sym == ctx.symbol:
                me = r
        if me is None or len(rets) < p["min_peers"]:
            return None
        return me - sum(rets) / len(rets)

    def should_enter(self, ctx):
        p = self.meta["params"]
        last = self._last_entry.get(ctx.symbol)
        if last is not None and (ctx.now - last).total_seconds() < p["cooldown_min"] * 60:
            return None
        s_now = self._stretch(ctx)
        if s_now is None or s_now < p["stretch_min"]:
            return None
        s_prev = self._stretch(ctx, back=p["fresh_bars"])
        if s_prev is None or s_prev >= p["stretch_min"] * p["fresh_frac"]:
            return None  # stretch is stale — the snap-back may already be done
        px = ctx.price
        sl = px * (1 + p["sl_frac"] * s_now)
        dist = (sl - px) / px
        if not (p["min_sl_pct"] <= dist <= p["max_sl_pct"]):
            return None
        self._last_entry[ctx.symbol] = ctx.now
        return Signal("short", sl=sl, tp=px * (1 - p["tp_frac"] * s_now),
                      reason=f"+{s_now * 100:.1f}% vs {ctx.group} basket 1h")

    def should_exit(self, ctx, pos):
        p = self.meta["params"]
        if (ctx.now - pos["entry_ts"]).total_seconds() / 60 >= p["max_hold_min"]:
            return "fade-timeout"
        s = self._stretch(ctx)
        if s is not None and s <= p["exit_stretch"]:
            return "stretch-collapsed"
        return None


STRATEGY = DispersionFade()
