"""Final probe (2026-08-03): range/ATR across every truncation point, with the
TRUNCATED ATR (not the complete-data ATR), plus the sl-consistent set.

For each live entry: for m = 0..14 rebuild the signal bar from minutes 0..m,
recompute ATR on that series, and report range/ATR against the 0.9 threshold and
|sl(m) - sl_stored|. The engine's true m lies in the sl-consistent set; the
clause must PASS there (the engine did enter), which constrains it further.
"""
import datetime as dt
import hashlib
import sys

import pandas as pd

sys.path.insert(0, "/srv")
from app.common import db  # noqa: E402
from app.common import indicators as ind  # noqa: E402

LOOKBACK_MIN, SL_ATR, LOW_BARS, MAX_RNG = 3500, 2.5, 48, 0.9
ENTRIES = [
    (525, "ACT_USDT", "2026-07-27 12:15"), (526, "UNI_USDT", "2026-07-27 21:15"),
    (527, "ORDI_USDT", "2026-07-28 17:15"), (528, "LDO_USDT", "2026-07-28 23:15"),
    (529, "ORDI_USDT", "2026-07-29 01:00"), (530, "JTO_USDT", "2026-07-29 03:15"),
    (531, "APE_USDT", "2026-07-29 03:30"), (532, "FLOKI_USDT", "2026-07-29 03:45"),
    (533, "APE_USDT", "2026-08-01 20:30"), (534, "ONDO_USDT", "2026-08-02 23:00"),
]

out = []
for pid, sym, bs in ENTRIES:
    bar = pd.Timestamp(bs, tz="UTC")
    sl_stored = db.q("SELECT sl FROM positions WHERE id=%s", (pid,))[0][0]
    rows = db.q("SELECT ts,o,h,l,c,v FROM candles WHERE symbol=%s AND tf='1m' "
                "AND ts>=%s AND ts<%s ORDER BY ts",
                (sym, bar - dt.timedelta(minutes=LOOKBACK_MIN), bar + dt.timedelta(minutes=15)))
    d1 = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).set_index("ts")
    d1.index = pd.to_datetime(d1.index, utc=True)
    d1 = d1.astype(float)

    cells = []
    for m in range(15):
        sub = d1[d1.index < bar + dt.timedelta(minutes=m + 1)]
        d15 = ind.resample(sub, "15min")
        if d15.index[-1] != bar:
            continue
        a = float(ind.atr(d15, 14).iloc[-1])
        sig = d15.iloc[-1]
        rng = (float(sig["h"]) - float(sig["l"])) / a
        lo12 = float(d15["l"].iloc[-(LOW_BARS + 1):-1].min())
        sl = min(float(sig["l"]), lo12) - SL_ATR * a
        cells.append((m, rng, abs(sl - sl_stored) / abs(sl_stored) < 1e-9))

    consistent = [c for c in cells if c[2]]
    pass_in_set = [c for c in consistent if c[1] <= MAX_RNG]
    full = [c for c in cells if c[0] == 14][0]
    rngs = ", ".join(f"m{c[0]}={c[1]:.3f}" for c in consistent)
    out.append(
        f"{pid}|{sym}|sl_consistent_m={[c[0] for c in consistent]}|"
        f"m14_consistent={full[2]}|rng_over_set[{rngs}]|"
        f"clause_passes_at_m={[c[0] for c in pass_in_set]}|"
        f"FULL_rng={full[1]:.3f}{'PASS' if full[1] <= MAX_RNG else 'FAIL'}")

txt = "\n".join(out)
print(txt)
print("MD5", hashlib.md5(txt.encode()).hexdigest())
