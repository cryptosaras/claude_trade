"""Whale absorption at lows (effort-vs-result divergence). After a multi-hour
slide, a 15m bar with huge volume but tiny range sitting on the 12h low means
heavy selling was fully absorbed without price progress — someone large is
buying retail capitulation. Go long with the whales; the bounce plays out over
~8h.

Event study 2026-07-06 (30d, 15m, 105 syms, net of 0.16%): raw 8h-hold after
these events +1.02% net (PF 3.09, n=175, positive in every regime and group),
BUT a tight SL below the absorption low destroys it (PF 0.58 — median stop
0.54%, chop consumes it before the bounce; same failure mode as
btc_shock_fade). Only a WIDE stop survives: SL 2.5 ATR below the low, TP 1.5R,
all three alt groups, BULL+BEAR only (SIDE died in the recent half): +0.31%
net/trade, PF 1.36, stable across halves (1.50/1.15). The mirror short side
(distribution churn at highs) was flat/unstable everywhere — killed.

Params were declared from the event study BEFORE the strategy-level gate run;
see report 2026-07-06 for the gate result."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class WhaleAbsorb(Strategy):
    meta = {
        "name": "whale_absorb",
        "version": 1,
        "description": "15m huge-volume/no-progress bar on the 12h low after a slide -> long the absorption",
        "groups": ["large_alts", "mid_alts", "memes"],
        # SIDE excluded: recent-half PF 0.93 raw / 0.17 with stops in the study
        "regimes": ["BULL", "BEAR"],
        "status": "paused",  # pre-gate; flip to incubating only if the gate passes
        "params": {
            "vol_mult": 2.5,       # signal 15m bar volume vs prior 20-bar average
            "max_rng_atr": 0.9,    # bar range at most this many ATR (no progress)
            "low_prox_atr": 0.3,   # bar low within this many ATR of the prior 12h low
            "low_bars": 48,        # 12h of 15m bars define the reference low
            "drop_6h": 0.015,      # 6h return must be <= -1.5% (a real slide)
            "atr_period": 14,
            "sl_atr_buf": 2.5,     # SL this far below the absorption low (wide, per study)
            "tp_r": 1.5,           # take-profit in R multiples
            "min_sl_pct": 0.005,   # skip if stop closer than 0.5%
            "max_sl_pct": 0.05,    # skip if stop further than 5%
            "max_hold_min": 480,   # bounce is spent by ~8h (h32 horizon in the study)
        },
    }

    @staticmethod
    def _closed_bars(ctx, df):
        # resample keeps the forming 15m bar; drop it so live and backtest agree
        if len(df) and df.index[-1] + dt.timedelta(minutes=15) > ctx.now:
            return df.iloc[:-1]
        return df

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = self._closed_bars(ctx, ctx.tf("15min"))
        if len(df) < p["low_bars"] + 25:
            return None
        sig = df.iloc[-1]
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        avg_vol = float(df["v"].iloc[-21:-1].mean())
        rng = float(sig["h"]) - float(sig["l"])
        if a <= 0 or avg_vol <= 0:
            return None
        if float(sig["v"]) < p["vol_mult"] * avg_vol:
            return None
        if rng > p["max_rng_atr"] * a:
            return None
        lo12 = float(df["l"].iloc[-(p["low_bars"] + 1):-1].min())
        if float(sig["l"]) > lo12 + p["low_prox_atr"] * a:
            return None
        drop = float(df["c"].iloc[-1]) / float(df["c"].iloc[-25]) - 1
        if drop > -p["drop_6h"]:
            return None
        px = ctx.price
        sl = min(float(sig["l"]), lo12) - p["sl_atr_buf"] * a
        dist = (px - sl) / px
        if not (p["min_sl_pct"] <= dist <= p["max_sl_pct"]):
            return None
        return Signal("long", sl=sl, tp=px + p["tp_r"] * (px - sl),
                      reason=f"absorption on 12h low, vol {float(sig['v'])/avg_vol:.1f}x, 6h {drop*100:.1f}%")

    def should_exit(self, ctx, pos):
        held_min = (ctx.now - pos["entry_ts"]).total_seconds() / 60
        if held_min >= self.meta["params"]["max_hold_min"]:
            return "bounce window over"
        return None


STRATEGY = WhaleAbsorb()
