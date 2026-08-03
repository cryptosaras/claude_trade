"""Bar-completeness enrichment census (pre-registered 2026-08-03, Measurement B).

Question: does a collector gap inside a 15m bar manufacture whale_absorb's
"huge volume, no price progress" signal? Test: are signal bars incomplete
(assembled from <15 1m candles) more often than all evaluable bars?

Reconstructs whale_absorb.should_enter exactly. No broker, no slots, no sizing.
"""
import datetime as dt
import hashlib
import json
import sys

import pandas as pd

sys.path.insert(0, "/srv")
from app.common import db  # noqa: E402
from app.common import indicators as ind  # noqa: E402

WIN_START = dt.datetime(2026, 7, 6, 0, 0, tzinfo=dt.timezone.utc)   # bar CLOSE >= this
WIN_END = dt.datetime(2026, 8, 2, 21, 0, tzinfo=dt.timezone.utc)    # bar CLOSE <= this
WARMUP_H = 30

P = dict(vol_mult=2.5, max_rng_atr=0.9, low_prox_atr=0.3, low_bars=48,
         drop_6h=0.015, atr_period=14, sl_atr_buf=2.5,
         min_sl_pct=0.005, max_sl_pct=0.05)

GROUPS = json.load(open("/tmp/groups.json"))
SYMS = sorted({s for g, ss in GROUPS.items() if g != "majors" for s in ss})


def load(sym):
    rows = db.q(
        "SELECT ts,o,h,l,c,v FROM candles WHERE symbol=%s AND tf='1m' "
        "AND ts>=%s AND ts<%s ORDER BY ts",
        (sym, WIN_START - dt.timedelta(hours=WARMUP_H), WIN_END),
    )
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df.astype(float)


def census():
    all_bars = 0
    all_incomplete = 0
    sig_rows = []
    per_sym = {}
    for sym in SYMS:
        d1 = load(sym)
        if d1 is None or len(d1) < 5000:
            continue
        d15 = ind.resample(d1, "15min")
        n1m = d1["c"].resample("15min").count().reindex(d15.index).fillna(0).astype(int)
        atr = ind.atr(d15, P["atr_period"])
        need = P["low_bars"] + 25
        sc, si = 0, 0
        for i in range(need, len(d15)):
            close_ts = d15.index[i] + dt.timedelta(minutes=15)
            if close_ts < WIN_START or close_ts > WIN_END:
                continue
            all_bars += 1
            sc += 1
            inc = n1m.iloc[i] < 15
            if inc:
                all_incomplete += 1
            sig = d15.iloc[i]
            a = float(atr.iloc[i])
            avg_vol = float(d15["v"].iloc[i - 20:i].mean())
            if a <= 0 or avg_vol <= 0:
                continue
            if float(sig["v"]) < P["vol_mult"] * avg_vol:
                continue
            if float(sig["h"]) - float(sig["l"]) > P["max_rng_atr"] * a:
                continue
            lo12 = float(d15["l"].iloc[i - P["low_bars"]:i].min())
            if float(sig["l"]) > lo12 + P["low_prox_atr"] * a:
                continue
            drop = float(sig["c"]) / float(d15["c"].iloc[i - 24]) - 1
            if drop > -P["drop_6h"]:
                continue
            px = float(sig["c"])
            sl = min(float(sig["l"]), lo12) - P["sl_atr_buf"] * a
            dist = (px - sl) / px
            if not (P["min_sl_pct"] <= dist <= P["max_sl_pct"]):
                continue
            si += 1
            sig_rows.append((sym, str(d15.index[i]), int(n1m.iloc[i]), bool(inc)))
        if sc:
            per_sym[sym] = (sc, si)

    sig_n = len(sig_rows)
    sig_inc = sum(1 for r in sig_rows if r[3])
    out = []
    out.append(f"window_bar_close [{WIN_START} .. {WIN_END}]")
    out.append(f"symbols_used {len(per_sym)} of {len(SYMS)}")
    out.append(f"ALL  evaluable_bars {all_bars}  incomplete {all_incomplete}  "
               f"rate {all_incomplete / all_bars * 100:.4f}%")
    out.append(f"SIG  bars {sig_n}  incomplete {sig_inc}  "
               f"rate {(sig_inc / sig_n * 100) if sig_n else 0:.4f}%")
    base = all_incomplete / all_bars if all_bars else 0
    sr = sig_inc / sig_n if sig_n else 0
    out.append(f"ENRICHMENT_RATIO {(sr / base) if base else float('nan'):.3f}")
    out.append("--- signal bars (sym, bar_start, n1m, incomplete) ---")
    for r in sorted(sig_rows):
        out.append("|".join(str(x) for x in r))
    txt = "\n".join(out)
    print(txt)
    print("MD5", hashlib.md5(txt.encode()).hexdigest())


census()
