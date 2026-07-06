"""Capitulation-bounce (liquidation-flush reversal), long-only: a fast down-move
ending in a volume-climax bar that closes back up off its low -> buy the bounce,
mid_alts+memes.

RETIRED 2026-07-06 — killed at the backtest gate in EVERY regime tested.
Gate (dev 21d / held-out 7d, judged by_regime, n large enough to trust):
  SIDE: PF 0.64 (n=179) / 0.82 (n=33)   win 37-42%
  BULL: PF 0.72 (n=104) / 0.76 (n=55)   win 39-42%
Both regimes, both groups, no edge. At a 1.6R target ~40% win loses before fees,
and the signal overtrades (5-9x/day, fees 1200-1650 per window). Root cause: the
trigger ("big drop + 2.5x volume + upper-half close") is too crude and fires on
noise. Contrast sweep_reclaim, whose BULL reclaim WORKS (PF 1.42) because it
requires sweeping a SPECIFIC 11h extreme level — the precise reference level is
the edge; generic magnitude is not. Do not revive without a precise level or a
real exhaustion signal (e.g. liquidation data we don't yet collect)."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class FlushReversal(Strategy):
    meta = {
        "name": "flush_reversal",
        "version": 1,
        "description": "5m volume-climax down-flush that reclaims its low -> long the bounce (BULL, mid_alts+memes)",
        "groups": ["mid_alts", "memes"],
        "regimes": ["BULL"],
        "status": "retired",  # failed the gate in SIDE and BULL, see docstring
        "params": {
            "flush_bars": 6,        # window (30m of 5m bars) the drop is measured over
            "flush_atr": 2.2,       # signal-bar low >= this many ATR below the close flush_bars ago
            "vol_mult": 2.5,        # climax volume vs 20-bar average
            "close_pos_min": 0.5,   # reclaim: close in the top half of the bar's range
            "atr_period": 14,
            "sl_atr_buf": 0.4,      # SL this far below the flush low
            "tp_r": 1.6,            # take-profit in R multiples of entry-to-SL
            "min_sl_pct": 0.005,    # skip if stop closer than 0.5% (fees dominate the R)
            "max_sl_pct": 0.03,     # skip if stop further than 3% (2R unreachable in 12h)
        },
    }

    @staticmethod
    def _closed_bars(ctx, df):
        # drop the still-forming 5m bar so live (partial last bar) and backtest
        # (complete last bar) evaluate the identical signal bar
        if len(df) and df.index[-1] + dt.timedelta(minutes=5) > ctx.now:
            return df.iloc[:-1]
        return df

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = self._closed_bars(ctx, ctx.tf("5min"))
        fb = p["flush_bars"]
        if len(df) < fb + 25:
            return None
        sig = df.iloc[-1]
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        rng = float(sig["h"]) - float(sig["l"])
        avg_vol = float(df["v"].iloc[-21:-1].mean())
        if a <= 0 or rng <= 0 or avg_vol <= 0:
            return None
        # 1) climax volume
        if float(sig["v"]) < p["vol_mult"] * avg_vol:
            return None
        # 2) magnitude: fast drop into the signal bar, and it made the local low
        ref_close = float(df["c"].iloc[-(fb + 1)])
        recent_low = float(df["l"].iloc[-fb:].min())
        if float(sig["l"]) > recent_low:            # signal bar isn't the flush low
            return None
        if ref_close - float(sig["l"]) < p["flush_atr"] * a:
            return None
        # 3) reclaim: bounced off the low, closes in upper half of its range
        close_pos = (float(sig["c"]) - float(sig["l"])) / rng
        if close_pos < p["close_pos_min"]:
            return None
        px = ctx.price
        sl = float(sig["l"]) - p["sl_atr_buf"] * a
        dist = (px - sl) / px
        if not (p["min_sl_pct"] <= dist <= p["max_sl_pct"]):
            return None
        return Signal("long", sl=sl, tp=px + p["tp_r"] * (px - sl),
                      reason=f"flush {(ref_close - sig['l']) / a:.1f} ATR, vol x{sig['v'] / avg_vol:.1f}, reclaimed")


STRATEGY = FlushReversal()
