"""Prior-day high/low (PDH/PDL) sweep-and-reclaim fade. The prior UTC day's
high and low are the single most-watched intraday levels in crypto — resting
stops cluster just beyond them. When a 5m bar spikes through the level on
volume and closes back inside, those stops have been consumed (a stop-hunt)
and price tends to revert away from the swept level. Trades the reclaim, never
the break; fixed reference (yesterday's H/L), unlike sweep_reclaim's rolling
11h extreme.

Event study (30d, 5m, net of 0.16% round-trip) showed the edge is entirely in
SIGNIFICANT sweeps: a loose 0.1-ATR poke fires ~1.4x/sym/day and is coin-flip
(PF ~0.8), but a >=0.6-ATR poke on >=2.5x volume lifts PF monotonically. The
group x regime split then isolated where it's real: mid_alts PF 1.39 (win 52%,
n=249) STABLE across both time-halves (h1 1.32 / h2 1.46); large_alts (0.87) and
memes (h1 0.53 / h2 1.38, unstable) dropped; BULL 0.85 dropped, BEAR 1.24 +
SIDE 1.17 kept.

Real-engine IN-SAMPLE backtest (full mechanics: funding, 2-pos/group cap, 12h
stop) is strong: 30d PF 1.31 (n=144, win 54%), BEAR 1.34 + SIDE 1.28. But this
is NOT a clean held-out — filter/group/regimes were all chosen by maximizing
over the same 30d, so 1.31 is selection-inflated; the one genuine forward slice
(last 7d) is only PF 1.02 and h1 only 1.15. True edge is likely ~1.1-1.2. On 30d
of 1m data a clean tune-old/test-new split isn't buildable, so live-forward
incubation IS the real OOS test. Judge ~Jul 11 on FORWARD live PF at >=20 trades
ONLY (do not anchor on 1.31); kill on forward PF < 1.15. See report 2026-07-06."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class PdhlSweep(Strategy):
    meta = {
        "name": "pdhl_sweep",
        "version": 1,
        "description": "5m sweep of prior-day H/L on volume that closes back inside -> fade it",
        "groups": ["mid_alts"],
        "regimes": ["SIDE", "BEAR"],
        "status": "incubating",
        "params": {
            "wick_atr": 0.6,      # poke must exceed PDH/PDL by this many ATR
            "vol_mult": 2.5,      # sweep-bar volume vs 20-bar average
            "atr_period": 14,
            "sl_atr_buf": 0.35,   # SL this far beyond the sweep wick
            "tp_r": 1.5,          # take-profit in R multiples of entry-to-SL
            "min_sl_pct": 0.004,  # skip if stop closer than 0.4% (fees dominate R)
            "max_sl_pct": 0.04,   # skip if stop further than 4% (2R unreachable in 12h)
        },
    }

    @staticmethod
    def _closed_bars(ctx, df):
        # resample keeps the forming 5m bar; drop it so live and backtest agree
        if len(df) and df.index[-1] + dt.timedelta(minutes=5) > ctx.now:
            return df.iloc[:-1]
        return df

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = self._closed_bars(ctx, ctx.tf("5min"))
        if len(df) < 60:
            return None
        sig = df.iloc[-1]
        ts = df.index[-1]
        # prior UTC day's high/low = the reference levels
        prev_day = (ts.normalize() - dt.timedelta(days=1))
        yday = df[(df.index >= prev_day) & (df.index < ts.normalize())]
        if len(yday) < 20:
            return None
        pdh = float(yday["h"].max())
        pdl = float(yday["l"].min())
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        avg_vol = float(df["v"].iloc[-21:-1].mean())
        if a <= 0 or avg_vol <= 0:
            return None
        if float(sig["v"]) < p["vol_mult"] * avg_vol:
            return None
        px = ctx.price
        hi, lo, cl = float(sig["h"]), float(sig["l"]), float(sig["c"])

        # sweep of PDH -> short the reclaim
        if hi > pdh + p["wick_atr"] * a and cl < pdh:
            sl = hi + p["sl_atr_buf"] * a
            dist = (sl - px) / px
            if p["min_sl_pct"] <= dist <= p["max_sl_pct"]:
                return Signal("short", sl=sl, tp=px - p["tp_r"] * (sl - px),
                              reason=f"swept PDH {pdh:.6g}, reclaimed")
        # sweep of PDL -> long the reclaim
        if lo < pdl - p["wick_atr"] * a and cl > pdl:
            sl = lo - p["sl_atr_buf"] * a
            dist = (px - sl) / px
            if p["min_sl_pct"] <= dist <= p["max_sl_pct"]:
                return Signal("long", sl=sl, tp=px + p["tp_r"] * (px - sl),
                              reason=f"swept PDL {pdl:.6g}, reclaimed")
        return None


STRATEGY = PdhlSweep()
