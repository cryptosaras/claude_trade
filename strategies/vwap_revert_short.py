"""Fade overextension above the daily VWAP — works when there is no bull trend.

v3 2026-07-18: raised stretch_atr floor 1.5 -> 4.5 on live forward evidence.
Bucketing all 278 live closed trades by the TRUE logged entry stretch (exact
join events.data->>'id' -> positions.id, so no fill-reconstruction confound)
shows the edge is entirely in stretch >= 4.5 ATR:
  mid_alts+SIDE (live engine): 1.5-2.5 PF 0.81 | 2.5-3.5 PF 1.17 |
  3.5-4.5 PF 1.02 (breakeven) | 4.5-6 PF 1.53 (+1728) | 6+ PF 1.49 (+1612).
Held-out time-split (first vs second half): >=4.5 is the ONLY bucket positive
in BOTH halves (PF 1.73 -> 1.33); 3.5-4.5 sign-flips (1.65 -> 0.51); <3.5 is
breakeven->losing. Mechanism: tp=vwap, so high-stretch entries have a far
target (big reward) vs the same 1.4-ATR stop -> reward:risk rises with stretch.
This also REFUTES the parabolic-cap hypothesis (6+ is the best bucket, not the
worst) -> vwap_revert_short_capped retired.
Status incubating per the validation gate (edited strategy). Judge on trades
with entry_ts AFTER the 2026-07-18T06:16Z deploy only (old-floor trades still
in the window). Pre-registered rule:
  - PROMOTE to active if forward mid_alts SIDE PF >= 1.15 at n >= 20 AND
    >= 5 days live (both, per the gate -- n>=20 alone arrives in ~2.5 days).
  - If forward PF < 1.0 at n >= 20: do NOT restore floor 1.5 (proven to bleed).
    Re-examine -- try a different floor or conclude the edge decayed.

2026-07-21: the rule FIRED and the re-examine is done -> PAUSED, retire
recommended (human call). Forward n=25, net -823, PF 0.50, win 16% vs the same
population pre-deploy n=111, PF 1.52. Three partitions were tested for a
rescuing filter; all three refute one and point the SAME way -- the forward
sample is uniformly worse, not worse in a subset:
  - BTC trend at entry: historical >=4.5 is BEST when BTC rises (PF 1.79,
    n=52) -- so "the forward window was a BTC uptrend" is NOT an excuse.
    Forward loses in both buckets (BTC rising PF 0.96 n=12; not rising 0.21 n=13).
  - Repeat entries (the TRBx5 / JASMYx4 / SYNx3 cascades): historically repeats
    are the GOOD half (PF 2.62 vs 1.01 for first entries) -- a cooldown would
    have cut the edge, not the losses. Forward both halves lose (0.26 / 0.66).
  - Symbol concentration: excl TRB still PF 0.69; excl TRB+JASMY still 0.82.
No floor change is supported either: >=4.5 remains the best historical bucket
(PF 1.33 n=136 vs 1.14 for all-stretch), and stretch does not separate forward
winners from losers. Do NOT edit params to chase this; the honest reading is
edge decay, on a still-modest forward n=25 over 2 days / 7 symbols.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _base import Signal, Strategy  # noqa: E402

from app.common import indicators as ind  # noqa: E402


class VwapRevertShort(Strategy):
    meta = {
        "name": "vwap_revert_short",
        "version": 3,
        "description": "Short 5m stretch >4.5 ATR above day-VWAP with RSI>68, target VWAP",
        # v2 2026-07-10: dropped large_alts on live evidence — mid_alts PF 1.50
        # (n=88, +2796) vs large_alts PF 0.68 (n=52, -1301). Live-paper split is
        # forward data on both groups, stronger than any backtest re-check.
        # v3 2026-07-18: stretch_atr 1.5 -> 4.5 (see module docstring). Edge is
        # entirely in stretch >= 4.5 ATR; sub-4.5 entries net-negative held-out.
        "groups": ["mid_alts"],
        "regimes": ["SIDE", "BEAR"],
        # 2026-07-21: paused, not retired -- pre-registered rule fired
        # (forward PF 0.50 at n=25). Retire is the recommendation, human call.
        "status": "paused",
        "params": {
            "stretch_atr": 4.5,
            "rsi_period": 14, "rsi_min": 68,
            "atr_period": 14, "sl_atr": 1.4,
        },
    }

    def should_enter(self, ctx):
        p = self.meta["params"]
        df = ctx.tf("5min")
        if len(df) < 80:
            return None
        vwap = float(ind.day_vwap(df).iloc[-1])
        a = float(ind.atr(df, p["atr_period"]).iloc[-1])
        px = ctx.price
        if a <= 0 or px - vwap < p["stretch_atr"] * a:
            return None
        r = float(ind.rsi(df["c"], p["rsi_period"]).iloc[-1])
        if r < p["rsi_min"]:
            return None
        return Signal("short", sl=px + p["sl_atr"] * a, tp=vwap,
                      reason=f"{(px - vwap) / a:.1f} ATR above VWAP, RSI {r:.0f}")


STRATEGY = VwapRevertShort()
