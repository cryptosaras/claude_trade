"""Liquidity-sweep reversal (stop-hunt fade). An obvious multi-hour extreme
holds resting stops; when a 5m wick sweeps it and the bar closes back inside
on elevated volume, the stops are consumed and price tends to revert away
from the level. Trades the reclaim, never the break, and only in trending
regimes where a directional crowd exists to trap: longs off swept lows in
BULL, shorts off swept highs in BEAR. v1 traded SIDE too — dev backtest
showed SIDE reclaims are chop noise (PF 0.55, n=58) while BULL/BEAR were
PF 1.42/1.07, so v2 removed SIDE."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class SweepReclaim(Strategy):
    meta = {
        "name": "sweep_reclaim",
        "version": 2,
        "description": "5m wick sweeps an 11h extreme and closes back inside on volume -> fade it",
        "groups": ["mid_alts", "memes"],
        # trend regimes only: longs off swept lows in BULL, shorts in BEAR
        "regimes": ["BULL", "BEAR"],
        "status": "paused",  # becomes incubating only after passing the backtest gate
        "params": {
            "lookback_bars": 132,   # 11h of 5m bars define the reference extreme
            "gap_bars": 3,          # extreme must predate the sweep by this many bars
            "wick_atr": 0.25,       # sweep must poke beyond the level by this many ATR
            "close_pos_min": 0.55,  # reclaim close in the top 45% of the bar (long side)
            "vol_mult": 1.5,        # sweep bar volume vs 20-bar average
            "atr_period": 14,
            "sl_atr_buf": 0.35,     # SL this far beyond the sweep wick
            "tp_r": 2.0,            # take-profit in R multiples of entry-to-SL
            "min_sl_pct": 0.004,    # skip if stop closer than 0.4% (fees dominate the R)
            "max_sl_pct": 0.025,    # skip if stop further than 2.5% (2R unreachable in 12h)
        },
    }

    @staticmethod
    def _closed_bars(ctx, df):
        # resample keeps the forming 5m bar; drop it so live (partial last bar)
        # and backtest (complete last bar) see the same signal bar
        if len(df) and df.index[-1] + dt.timedelta(minutes=5) > ctx.now:
            return df.iloc[:-1]
        return df

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = self._closed_bars(ctx, ctx.tf("5min"))
        n, gap = p["lookback_bars"], p["gap_bars"]
        if len(df) < n + gap + 22:
            return None
        sig = df.iloc[-1]
        ref = df.iloc[-(n + gap + 1):-(gap + 1)]
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        rng = float(sig["h"]) - float(sig["l"])
        avg_vol = float(df["v"].iloc[-21:-1].mean())
        if a <= 0 or rng <= 0 or avg_vol <= 0:
            return None
        if float(sig["v"]) < p["vol_mult"] * avg_vol:
            return None
        close_pos = (float(sig["c"]) - float(sig["l"])) / rng
        px = ctx.price

        if ctx.regime == "BULL":
            ref_low = float(ref["l"].min())
            if (float(sig["l"]) < ref_low - p["wick_atr"] * a
                    and float(sig["c"]) > ref_low
                    and close_pos >= p["close_pos_min"]):
                sl = float(sig["l"]) - p["sl_atr_buf"] * a
                dist = (px - sl) / px
                if p["min_sl_pct"] <= dist <= p["max_sl_pct"]:
                    return Signal("long", sl=sl, tp=px + p["tp_r"] * (px - sl),
                                  reason=f"swept 11h low {ref_low:.6g}, reclaimed")

        if ctx.regime == "BEAR":
            ref_high = float(ref["h"].max())
            if (float(sig["h"]) > ref_high + p["wick_atr"] * a
                    and float(sig["c"]) < ref_high
                    and close_pos <= 1 - p["close_pos_min"]):
                sl = float(sig["h"]) + p["sl_atr_buf"] * a
                dist = (sl - px) / px
                if p["min_sl_pct"] <= dist <= p["max_sl_pct"]:
                    return Signal("short", sl=sl, tp=px - p["tp_r"] * (sl - px),
                                  reason=f"swept 11h high {ref_high:.6g}, rejected")
        return None


STRATEGY = SweepReclaim()
