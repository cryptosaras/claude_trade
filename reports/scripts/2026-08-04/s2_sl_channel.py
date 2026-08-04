"""S2, split out so it can be twinned (2026-08-04).

pipeline_health.py bundles S1 (staleness, time-varying -- cannot twin) with S2
(deterministic). This is S2 alone: extend the Aug 3 sl-channel truncation table
from 10 live entries to 12, method unchanged from
reports/scripts/2026-08-03/sl_discriminator.py.
"""
import datetime as dt
import hashlib
import sys

import pandas as pd

sys.path.insert(0, "/srv")
from app.common import db  # noqa: E402
from app.common import indicators as ind  # noqa: E402

SLIP, LOOKBACK_MIN, SL_ATR, LOW_BARS = 0.0003, 3500, 2.5, 48
NEW_ENTRIES = [(535, "ORDI_USDT", "2026-08-03 08:00"),
               (536, "INJ_USDT", "2026-08-03 08:45")]

out = []
for pid, sym, bs in NEW_ENTRIES:
    bar = pd.Timestamp(bs, tz="UTC")
    sl_stored, ep, ets = db.q(
        "SELECT sl, entry_price, entry_ts FROM positions WHERE id=%s", (pid,))[0]
    ctx_price = float(ep) / (1 + SLIP)
    rows = db.q("SELECT ts,o,h,l,c,v FROM candles WHERE symbol=%s AND tf='1m' "
                "AND ts>=%s AND ts<%s ORDER BY ts",
                (sym, bar - dt.timedelta(minutes=LOOKBACK_MIN),
                 bar + dt.timedelta(minutes=15)))
    d1 = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).set_index("ts")
    d1.index = pd.to_datetime(d1.index, utc=True)
    d1 = d1.astype(float)

    inbar = d1[(d1.index >= bar) & (d1.index < bar + dt.timedelta(minutes=15))]
    pm = [i for i, c in enumerate(inbar["c"])
          if abs(float(c) - ctx_price) / ctx_price < 1e-9]
    near = min(abs(float(c) - ctx_price) / ctx_price for c in inbar["c"])

    res = []
    for m in range(15):
        sub = d1[d1.index < bar + dt.timedelta(minutes=m + 1)]
        d15 = ind.resample(sub, "15min")
        if d15.index[-1] != bar:
            continue
        a = float(ind.atr(d15, 14).iloc[-1])
        lo12 = float(d15["l"].iloc[-(LOW_BARS + 1):-1].min())
        sl = min(float(d15["l"].iloc[-1]), lo12) - SL_ATR * a
        rng = (float(d15["h"].iloc[-1]) - float(d15["l"].iloc[-1])) / a
        res.append((m, sl, abs(sl - float(sl_stored)) / abs(float(sl_stored)), rng))

    best = min(res, key=lambda r: r[2])
    exact = [r[0] for r in res if r[2] < 1e-9]
    m14 = [r for r in res if r[0] == 14][0]
    offset = (ets - (bar + dt.timedelta(minutes=15))).total_seconds() / 60
    out.append(
        f"{pid} {sym:10s} bar {bs}  entry_offset {offset:+.2f} min\n"
        f"    price_channel: exact_minutes {pm} (nearest rel {near:.1e})\n"
        f"    sl_channel:    best_m={best[0]} (rel {best[2]:.2e})  exact_m={exact}  "
        f"m14_rel={m14[2]:.2e}\n"
        f"    range/ATR at consistent m: "
        f"{ {r[0]: round(r[3], 3) for r in res if r[2] < 1e-9} }  "
        f"complete_bar {m14[3]:.3f}  (clause limit 0.9)\n"
        f"    VERDICT {'TRUNCATED' if 14 not in exact else 'UNDETERMINED (m=14 possible)'}")

txt = "\n".join(out)
print(txt)
print("MD5", hashlib.md5(txt.encode()).hexdigest())
