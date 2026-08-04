"""S1 + S2 supplementary measurements (pre-registered 2026-08-04).

S1: 1m-candle staleness across group symbols, 3 snapshots 20s apart -- the same
    shape as the 2026-08-03 measurement (which found mean 1.2-1.7 min, max 2.1).
S2: extend the Aug 3 sl-channel truncation table from 10 live entries to 12,
    using the identical method: scan truncation point m = 0..14, rebuild the
    signal bar from minutes 0..m, recompute ATR on THAT series, and keep the m
    whose implied sl = min(bar_low, lo12) - 2.5*ATR reproduces the STORED sl.
    Also recover the price channel: entry_price / (1 + slippage) = ctx.price =
    the newest 1m close the engine had.
"""
import datetime as dt
import hashlib
import json
import sys
import time

import pandas as pd

sys.path.insert(0, "/srv")
from app.common import db  # noqa: E402
from app.common import indicators as ind  # noqa: E402

SLIP, LOOKBACK_MIN, SL_ATR, LOW_BARS = 0.0003, 3500, 2.5, 48
NEW_ENTRIES = [                       # the two live entries opened since Aug 3
    (535, "ORDI_USDT", "2026-08-03 08:00"),
    (536, "INJ_USDT", "2026-08-03 08:45"),
]

GROUPS = json.load(open("/tmp/groups.json"))
SYMS = sorted({s for g, ss in GROUPS.items() for s in ss})[:20]

out = []

# ---------------- S1: staleness ----------------
out.append("=== S1 staleness (group symbols, 1m candles) ===")
for snap in range(3):
    rows = db.q(
        "SELECT symbol, EXTRACT(EPOCH FROM (now() - max(ts)))/60 FROM candles "
        "WHERE tf='1m' AND symbol = ANY(%s) GROUP BY symbol", (SYMS,))
    v = sorted(float(r[1]) for r in rows)
    now = db.q("SELECT now()")[0][0]
    out.append(f"  snapshot {snap} {now:%H:%M:%S}Z  n={len(v)}  mean {sum(v)/len(v):.2f} min  "
               f"median {v[len(v)//2]:.2f}  max {max(v):.2f}  "
               f">1min {sum(1 for x in v if x > 1)}/{len(v)}  "
               f">3min {sum(1 for x in v if x > 3)}/{len(v)}")
    if snap < 2:
        time.sleep(20)

# ---------------- S2: sl-channel on the two new entries ----------------
out.append("=== S2 sl-channel truncation probe, new live entries ===")
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
    pm = [i for i, (_, r) in enumerate(inbar.iterrows())
          if abs(float(r["c"]) - ctx_price) / ctx_price < 1e-9]
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
        f"  {pid} {sym:10s} bar {bs}  entry_offset {offset:+.2f} min\n"
        f"      price_channel: exact_minutes {pm} (nearest rel {near:.1e})\n"
        f"      sl_channel:    best_m={best[0]} (rel {best[2]:.2e})  exact_m={exact}  "
        f"m14_rel={m14[2]:.2e}\n"
        f"      range/ATR at consistent m: "
        f"{ {r[0]: round(r[3], 3) for r in res if r[2] < 1e-9} }  "
        f"complete_bar {m14[3]:.3f}  (clause limit 0.9)\n"
        f"      VERDICT {'TRUNCATED' if 14 not in exact else 'COMPLETE_BAR_POSSIBLE'}")

txt = "\n".join(out)
print(txt)
print("MD5", hashlib.md5(txt.encode()).hexdigest())
