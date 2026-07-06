"""BTC-shock overshoot fade, long-only: when BTC flushes >=0.35% in a single
1m bar during BEAR, alts get dragged down in the SAME minute (lag-0 corr
0.75-0.83) and overshoot; the overshoot mean-reverts over the next 2-5h.

Origin: user idea "Follow BTC" (alts follow big BTC moves). Event study
2026-07-06 (30d, 73 syms): FOLLOWING the move loses at every threshold/group/
horizon (alts move at lag 0, then retrace — confirms the dead BTC lead-lag
item). The tradeable signal is the FADE of the regime-aligned overshoot:
per-event medians (n=17 aligned events, 14 distinct days), fade net of 0.16%:
  large_alts 4h +1.15% (hit 65%) | mid_alts +0.76% (65%) | memes +1.20% (59%)
Edge concentrates in BEAR down-flushes (10/14 events positive at 4h); the
BULL-side fade was 1-for-3 -> BEAR only, long only. Fat left tail when the
flush continues (-2.4..-5.9%) -> SL below the flush low is mandatory.
The BTC 1m shock is the precise trigger flush_reversal lacked (its lesson:
generic magnitude fires on noise; a specific reference event is the edge).

RETIRED 2026-07-06 — killed at the backtest gate, never traded live.
Gate: 8-variant param scan on dev 21d; tight v1 SL (0.4 ATR, min 0.6%) PF 0.87
(n=37) — stopped out before the 2-4h bounce, fees > gross. Wide-SL family
(1.0 ATR, min 1%) was robust across TPs on dev: PF 1.37/1.98/1.22 at
tp_r 1.0/1.5/2.2, no-TP 4h-hold 3.05 (fragile: 5h sibling 1.56). Chose
tp_r 1.5 (dev PF 1.98, n=36, win 64%). HELD-OUT (days 30..21 ago, excluded
from the scan): PF 0.93, n=19, win 37% — < 1.1 gate, reject. Same
dev-inflation pattern as dispersion_fade. The per-event medians are real but
~0.16% fees + SL/TP mechanics eat the ~1% median bounce; win rate collapsed
out of the tuning window. Do not revive without a sharper entry filter proven
on NEW data (candidate: gate on concurrent OI drop — the queued
OI-confirmed-flush item, ~Jul 19)."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class BtcShockFade(Strategy):
    meta = {
        "name": "btc_shock_fade",
        "version": 1,
        "description": "BTC 1m flush >=0.35% in BEAR -> long alts' overshoot bounce (2-5h)",
        "groups": ["large_alts", "mid_alts", "memes"],
        "regimes": ["BEAR"],
        "status": "retired",  # failed the held-out gate (PF 0.93 < 1.1), see docstring
        "params": {
            "btc_shock_pct": 0.0035,  # |BTC 1m return| that counts as a shock (down only)
            "fresh_min": 6,           # shock bar must be this recent (>= backtest step of 5m)
            "low_lookback_min": 45,   # SL reference: alt's low over this window (the flush low)
            "atr_period": 14,         # 5m ATR for the SL buffer
            "sl_atr_buf": 1.0,        # SL this far below the reference low (as gate-tested)
            "tp_r": 1.5,              # take-profit in R multiples of entry-to-SL
            "min_sl_pct": 0.01,       # skip if stop closer than 1% (as gate-tested)
            "max_sl_pct": 0.03,       # skip if stop further than 3%
            "max_hold_min": 300,      # bounce is spent by ~4-5h (per-event medians peak at 4h)
        },
    }

    @staticmethod
    def _closed_1m(ctx, df):
        # drop the still-forming 1m bar so live (partial last bar) and backtest
        # (complete last bar) evaluate identical signal bars
        if len(df) and df.index[-1] + dt.timedelta(minutes=1) > ctx.now:
            return df.iloc[:-1]
        return df

    def should_enter(self, ctx):
        p = self.meta["params"]
        btc = ctx.btc
        if btc is None or len(btc) < p["fresh_min"] + 2:
            return None
        btc = self._closed_1m(ctx, btc)
        tail = btc.iloc[-(p["fresh_min"] + 1):]
        rets = tail["c"].pct_change().iloc[1:]
        cutoff = ctx.now - dt.timedelta(minutes=p["fresh_min"])
        recent = rets[rets.index >= cutoff]
        if len(recent) == 0 or recent.min() > -p["btc_shock_pct"]:
            return None
        shock = recent.min()
        df5 = ctx.tf("5min")
        if len(df5) < p["atr_period"] + 2:
            return None
        a = float(ind.atr(df5, p["atr_period"]).iloc[-1])
        if a <= 0:
            return None
        flush_low = float(ctx.df["l"].iloc[-p["low_lookback_min"]:].min())
        px = ctx.price
        sl = flush_low - p["sl_atr_buf"] * a
        dist = (px - sl) / px
        if not (p["min_sl_pct"] <= dist <= p["max_sl_pct"]):
            return None
        return Signal("long", sl=sl, tp=px + p["tp_r"] * (px - sl),
                      reason=f"BTC 1m shock {shock * 100:.2f}%, fading the drag")

    def should_exit(self, ctx, pos):
        held_min = (ctx.now - pos["entry_ts"]).total_seconds() / 60
        if held_min >= self.meta["params"]["max_hold_min"]:
            return "bounce window over"
        return None


STRATEGY = BtcShockFade()
