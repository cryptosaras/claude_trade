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
        "status": "incubating",
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
