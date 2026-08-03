"""Measurement A, second half (pre-registered 2026-08-03): for each of the 10
live whale_absorb entries, re-evaluate every should_enter clause on today's data
and report the value against its threshold, plus the 1m candle count of the bar.
"""
import datetime as dt
import hashlib
import sys

import pandas as pd

sys.path.insert(0, "/srv")
from app.common import db  # noqa: E402
from app.common import indicators as ind  # noqa: E402

P = dict(vol_mult=2.5, max_rng_atr=0.9, low_prox_atr=0.3, low_bars=48,
         drop_6h=0.015, atr_period=14, sl_atr_buf=2.5,
         min_sl_pct=0.005, max_sl_pct=0.05)

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
    rows = db.q(
        "SELECT ts,o,h,l,c,v FROM candles WHERE symbol=%s AND tf='1m' "
        "AND ts>=%s AND ts<%s ORDER BY ts",
        (sym, bar - dt.timedelta(hours=30), bar + dt.timedelta(minutes=15)),
    )
    d1 = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).set_index("ts")
    d1.index = pd.to_datetime(d1.index, utc=True)
    d1 = d1.astype(float)
    d15 = ind.resample(d1, "15min")
    n1m = int(d1["c"].resample("15min").count().reindex(d15.index).fillna(0).iloc[-1])
    i = len(d15) - 1
    assert d15.index[i] == bar, f"{sym} bar mismatch {d15.index[i]} != {bar}"
    a = float(ind.atr(d15, P["atr_period"]).iloc[i])
    sig = d15.iloc[i]
    avg_vol = float(d15["v"].iloc[i - 20:i].mean())
    volx = float(sig["v"]) / avg_vol
    rng_atr = (float(sig["h"]) - float(sig["l"])) / a
    lo12 = float(d15["l"].iloc[i - P["low_bars"]:i].min())
    prox = (float(sig["l"]) - lo12) / a
    drop = float(sig["c"]) / float(d15["c"].iloc[i - 24]) - 1
    px = float(sig["c"])
    sl = min(float(sig["l"]), lo12) - P["sl_atr_buf"] * a
    dist = (px - sl) / px
    ok = (volx >= P["vol_mult"] and rng_atr <= P["max_rng_atr"]
          and prox <= P["low_prox_atr"] and drop <= -P["drop_6h"]
          and P["min_sl_pct"] <= dist <= P["max_sl_pct"])
    out.append(f"{pid}|{sym}|{bs}|n1m={n1m}|vol={volx:.2f}x(>=2.5)|"
               f"rng={rng_atr:.3f}ATR(<=0.9)|prox={prox:.3f}ATR(<=0.3)|"
               f"drop={drop*100:.2f}%(<=-1.5)|sl={dist*100:.2f}%(0.5-5.0)|"
               f"REPRODUCES={ok}")

txt = "\n".join(out)
print(txt)
print("MD5", hashlib.md5(txt.encode()).hexdigest())
