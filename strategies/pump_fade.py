"""Parabolic pump fade. Fast movers (memes, small mid-caps) that go vertical
attract late chasers; when the vertical leg cracks — a lower high followed by
a close below the prior bar's low, with the peak only a few bars old — the
chasers are trapped and the unwind is fast. Short the first crack, never the
strength: entry requires the structural break, not just an extended move.
SL sits above the pump high (nobody rational averages a short above it);
TP is R-based so the fee-drag guard (min_sl_pct) keeps R meaningful."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class PumpFade(Strategy):
    meta = {
        "name": "pump_fade",
        "version": 1,
        "description": "Short the first lower-high crack after a >=5%/1h parabolic run",
        "groups": ["mid_alts", "memes"],
        "regimes": ["BULL", "SIDE", "BEAR"],
        "status": "paused",  # pending validation gate
        "params": {
            "run_bars": 12,        # pump window: 12 x 5m = 1h
            "run_pct": 0.05,       # min gain low->high inside the window
            "peak_within": 4,      # pump high must be <= this many bars old
            "atr_period": 14,
            "sl_atr_buf": 0.5,     # SL this far above the pump high
            "tp_r": 1.8,           # take-profit in R multiples
            "min_sl_pct": 0.005,   # skip if stop closer than 0.5% (fees dominate)
            "max_sl_pct": 0.035,   # skip if stop further than 3.5%
        },
    }

    @staticmethod
    def _closed_bars(ctx, df):
        # drop the forming 5m bar so live and backtest see the same signal bar
        if len(df) and df.index[-1] + dt.timedelta(minutes=5) > ctx.now:
            return df.iloc[:-1]
        return df

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = self._closed_bars(ctx, ctx.tf("5min"))
        n = p["run_bars"]
        if len(df) < n + p["atr_period"] + 5:
            return None
        win = df.iloc[-n:]
        run_low = float(win["l"].min())
        run_high = float(win["h"].max())
        if run_low <= 0 or (run_high - run_low) / run_low < p["run_pct"]:
            return None
        # the vertical leg must be fresh: peak within the last few bars,
        # and the low must come before the high (an up-leg, not a dump)
        peak_age = n - 1 - int(win["h"].values.argmax())
        if peak_age > p["peak_within"] or int(win["l"].values.argmin()) >= int(win["h"].values.argmax()):
            return None
        # the crack: signal bar makes a lower high and closes below prior bar's low
        sig, prev = df.iloc[-1], df.iloc[-2]
        if not (float(sig["h"]) < float(prev["h"]) and float(sig["c"]) < float(prev["l"])):
            return None
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        if a <= 0:
            return None
        px = ctx.price
        sl = run_high + p["sl_atr_buf"] * a
        dist = (sl - px) / px
        if not (p["min_sl_pct"] <= dist <= p["max_sl_pct"]):
            return None
        return Signal("short", sl=sl, tp=px - p["tp_r"] * (sl - px),
                      reason=f"pump +{100 * (run_high - run_low) / run_low:.1f}%/1h cracked")


STRATEGY = PumpFade()
