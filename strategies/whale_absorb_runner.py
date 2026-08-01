"""whale_absorb's entry, with the take-profit clip removed. ONE variable changed.

Why this exists (reasoning committed in reports/2026-08-01.md BEFORE any run):

whale_absorb's own event study (2026-07-06, 30d, 15m, 105 syms, net of 0.16%)
reported the raw 8h hold after the absorption event at **+1.02% net per trade**
(PF 3.09, n=175), but the deployed strategy — SL 2.5 ATR, **TP 1.5R** — delivers
only **+0.31% net per trade**. Two thirds of the studied edge is left on the
table by the exit, not by the entry.

The 2026-07-31 report measured that friction is a fixed **~0.16% of notional per
trade** (0.10% fees+funding, plus 0.03%/side slippage inside the fill price) and
does not scale with how far the stop or target sits. Two consequences, and they
are the whole thesis:

  - Scaling a **zero** gross edge by widening the trade changes nothing. That is
    exactly why three sessions of stop-distance tuning on `range_fader` /
    `sweep_reclaim` (gross edge ~0.00%) found nothing, and this file does not
    contradict that finding — it is the case that finding carved out.
  - Scaling a **positive** gross edge lifts it against fixed cost. The absorption
    event is the one signal in this repo with a measured positive raw hold.

So: let the bounce run to the 8h time exit instead of clipping it at 1.5R.

PRE-DECLARED, not tuned: `tp_r: 6.0`. The value expresses "do not clip" (a 6R
move inside 8h is rare, so the time exit dominates); it was fixed from the study
above before either backtest window was run, so BOTH windows are confirmation
runs and there is no tuning window. Every entry parameter is whale_absorb's
verbatim — vol_mult, max_rng_atr, low_prox_atr, low_bars, drop_6h, sl_atr_buf
and both sl_pct bounds are untouched, so any difference in result is attributable
to the exit alone.

Caveat stated up front: the ENTRY conjunction inherits whale_absorb's in-sample
history (its params came from a study ending 2026-07-06, and Jul 13-27 was
already spent as its gate's out-of-sample window). The EXIT change is untuned on
any window. So both windows are held-out for the exit and neither is clean for
the entry.

Status stays `retired` until the gate is passed — it must not take live slots
while it is being measured.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class WhaleAbsorbRunner(Strategy):
    meta = {
        "name": "whale_absorb_runner",
        "version": 1,
        "description": "whale_absorb's absorption entry, held to the 8h time exit instead of clipped at 1.5R",
        "groups": ["large_alts", "mid_alts", "memes"],
        "regimes": ["BULL", "BEAR"],
        "status": "retired",
        "params": {
            # --- entry: verbatim from whale_absorb v1, do not touch ---
            "vol_mult": 2.5,
            "max_rng_atr": 0.9,
            "low_prox_atr": 0.3,
            "low_bars": 48,
            "drop_6h": 0.015,
            "atr_period": 14,
            "sl_atr_buf": 2.5,
            "min_sl_pct": 0.005,
            "max_sl_pct": 0.05,
            # --- exit: the ONE changed variable ---
            "tp_r": 6.0,           # whale_absorb uses 1.5; pre-declared, not tuned
            "max_hold_min": 480,   # 8h, the study's horizon; under the engine's 12h cap
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


STRATEGY = WhaleAbsorbRunner()
