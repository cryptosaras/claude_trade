"""Truncated-bar probe v2 (2026-08-03).

For each live whale_absorb entry: locate every 1m candle in [bar-15m, bar+15m)
whose close equals the price the engine saw (entry_price / (1+slippage)), then
re-evaluate the binding `range <= 0.9 ATR` clause on the bar TRUNCATED at that
minute vs the COMPLETE bar. Truncation shrinks the range; the clause keys on it.
"""
import datetime as dt
import hashlib
import sys

import pandas as pd

sys.path.insert(0, "/srv")
from app.common import db  # noqa: E402
from app.common import indicators as ind  # noqa: E402

SLIP = 0.0003
MAX_RNG_ATR = 0.9
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
    ep = db.q("SELECT entry_price FROM positions WHERE id=%s", (pid,))[0][0]
    px = ep / (1 + SLIP)
    rows = db.q(
        "SELECT ts,h,l,c FROM candles WHERE symbol=%s AND tf='1m' AND ts>=%s AND ts<%s "
        "ORDER BY ts", (sym, bar - dt.timedelta(minutes=15), bar + dt.timedelta(minutes=15)))
    idx = {int((r[0] - bar).total_seconds() // 60): r for r in rows}
    inbar = [idx[m] for m in range(15) if m in idx]
    exact = [m for m in sorted(idx) if abs(idx[m][3] - px) / px < 1e-9]
    near = min(sorted(idx), key=lambda m: abs(idx[m][3] - px))
    reldiff = abs(idx[near][3] - px) / px

    # ATR on complete 15m data up to and including this bar
    c1 = db.q("SELECT ts,o,h,l,c,v FROM candles WHERE symbol=%s AND tf='1m' "
              "AND ts>=%s AND ts<%s ORDER BY ts",
              (sym, bar - dt.timedelta(hours=30), bar + dt.timedelta(minutes=15)))
    d1 = pd.DataFrame(c1, columns=["ts", "o", "h", "l", "c", "v"]).set_index("ts")
    d1.index = pd.to_datetime(d1.index, utc=True)
    d15 = ind.resample(d1.astype(float), "15min")
    atr = float(ind.atr(d15, 14).iloc[-1])

    hi_f = max(r[1] for r in inbar); lo_f = min(r[2] for r in inbar)
    full_ratio = (hi_f - lo_f) / atr
    # candidate truncation points: every exact match inside the bar (>=0 and <15)
    cands = [m for m in exact if 0 <= m < 15] or [near]
    parts = []
    for m in cands:
        sub = [idx[k] for k in range(m + 1) if k in idx]
        if not sub:
            continue
        hi = max(r[1] for r in sub); lo = min(r[2] for r in sub)
        parts.append(f"m{m}:rng={(hi - lo) / atr:.3f}ATR{'PASS' if (hi - lo) / atr <= MAX_RNG_ATR else 'FAIL'}")
    out.append(f"{pid}|{sym}|{bs}|exact_minutes={exact}|nearest_m={near}(rel {reldiff:.1e})|"
               f"FULL rng={full_ratio:.3f}ATR{'PASS' if full_ratio <= MAX_RNG_ATR else 'FAIL'}|"
               f"TRUNC {' '.join(parts)}")

txt = "\n".join(out)
print(txt)
print("MD5", hashlib.md5(txt.encode()).hexdigest())
