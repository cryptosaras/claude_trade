"""Independent discriminator: truncation vs. candle revision (2026-08-03).

positions.sl encodes the engine's view through a DIFFERENT channel than
ctx.price: sl = min(sig_low, lo12) - 2.5*ATR, so it depends on the signal bar's
LOW and on ATR, not on its close. Scan every truncation point m = 0..14, rebuild
the signal bar from minutes 0..m, and see which m reproduces the STORED sl.

If the sl-implied minute agrees with the price-implied minute -> truncation.
If sl matches m=14 (complete bar) while price matched an earlier minute
-> the stored candles were revised and the price match was coincidence.
"""
import datetime as dt
import hashlib
import sys

import pandas as pd

sys.path.insert(0, "/srv")
from app.common import db  # noqa: E402
from app.common import indicators as ind  # noqa: E402

SLIP, LOOKBACK_MIN, SL_ATR, LOW_BARS = 0.0003, 3500, 2.5, 48
ENTRIES = [
    (525, "ACT_USDT", "2026-07-27 12:15", 5), (526, "UNI_USDT", "2026-07-27 21:15", 9),
    (527, "ORDI_USDT", "2026-07-28 17:15", 13), (528, "LDO_USDT", "2026-07-28 23:15", 10),
    (529, "ORDI_USDT", "2026-07-29 01:00", 4), (530, "JTO_USDT", "2026-07-29 03:15", 11),
    (531, "APE_USDT", "2026-07-29 03:30", 14), (532, "FLOKI_USDT", "2026-07-29 03:45", 14),
    (533, "APE_USDT", "2026-08-01 20:30", 11), (534, "ONDO_USDT", "2026-08-02 23:00", 12),
]

out = []
for pid, sym, bs, price_m in ENTRIES:
    bar = pd.Timestamp(bs, tz="UTC")
    sl_stored, ep = db.q("SELECT sl, entry_price FROM positions WHERE id=%s", (pid,))[0]
    rows = db.q("SELECT ts,o,h,l,c,v FROM candles WHERE symbol=%s AND tf='1m' "
                "AND ts>=%s AND ts<%s ORDER BY ts",
                (sym, bar - dt.timedelta(minutes=LOOKBACK_MIN), bar + dt.timedelta(minutes=15)))
    d1 = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).set_index("ts")
    d1.index = pd.to_datetime(d1.index, utc=True)
    d1 = d1.astype(float)

    res = []
    for m in range(15):
        sub = d1[d1.index < bar + dt.timedelta(minutes=m + 1)]
        d15 = ind.resample(sub, "15min")
        if d15.index[-1] != bar:
            continue
        a = float(ind.atr(d15, 14).iloc[-1])
        lo12 = float(d15["l"].iloc[-(LOW_BARS + 1):-1].min())
        sl = min(float(d15["l"].iloc[-1]), lo12) - SL_ATR * a
        res.append((m, sl, abs(sl - sl_stored) / abs(sl_stored), a))

    best = min(res, key=lambda r: r[2])
    exact = [r[0] for r in res if r[2] < 1e-9]
    spread = (max(r[3] for r in res) - min(r[3] for r in res)) / min(r[3] for r in res)
    full = [r for r in res if r[0] == 14][0]
    out.append(
        f"{pid}|{sym}|price_m={price_m}|SL_best_m={best[0]}(rel {best[2]:.2e})|"
        f"SL_exact_m={exact}|SL_at_m14 rel={full[2]:.2e}|ATR_spread_over_m={spread * 100:.2f}%|"
        f"VERDICT={'TRUNCATED@' + str(best[0]) if best[0] != 14 else 'COMPLETE_BAR'}"
        f"{' AGREES_WITH_PRICE' if best[0] == price_m else ' DISAGREES_WITH_PRICE'}")

txt = "\n".join(out)
print(txt)
print("MD5", hashlib.md5(txt.encode()).hexdigest())
