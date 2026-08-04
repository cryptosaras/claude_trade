"""The engine's REALIZED signal set (2026-08-04, mid-session refinement).

Forced by reading app/engine/core.py:133-160: `should_enter` is called on every
symbol on EVERY 20s tick, with no per-bar memo, and bar T stays the newest
closed bar for a full 15 minutes while the collector fills it in. So the engine
gets repeated attempts at the same bar at decreasing truncation, and fires on
the FIRST tick where the conjunction holds.

Consequence: the defect is ADDITIVE, not substitutive. A signal that needs the
complete bar is not missed -- it fires a minute or two later. The wedge is
therefore REALIZED(R) vs SPEC(m=0), where

    fires_at = {m in 0..R : fire(m)},  engine trades at m* = max(fires_at)

(larger m = fewer minutes present = earlier wall-clock tick), and R is the
truncation depth at the first tick after the bar boundary. Today's staleness is
1.60-1.98 min mean / 2.77 max with entry offsets ~0.09 min, so R in {2,3}.

Also prices fix design (b) -- offset the decision tick past the collector
refresh -- by re-entering the SPEC set k minutes after the bar boundary.

NOT pre-registered: R in {2,3}, the delayed-entry cells and the clause
attribution are labelled as a refinement in the report.
"""
import datetime as dt
import hashlib
import json
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, "/srv")
from app.common import db  # noqa: E402
from app.common import indicators as ind  # noqa: E402

WIN_START = dt.datetime(2026, 7, 6, 0, 0, tzinfo=dt.timezone.utc)
WIN_END = dt.datetime(2026, 8, 3, 21, 0, tzinfo=dt.timezone.utc)
WARMUP_H, FWD_MIN, COST, MIN_COV = 30, 480, 0.16, 0.90
DEPTHS = [0, 1, 2, 3, 4, 5]
LAGS = [1, 2, 3, 5]              # minutes past the bar boundary, for design (b)
CLAUSES = ("vol", "rng", "low", "drop", "dist")

P = dict(vol_mult=2.5, max_rng_atr=0.9, low_prox_atr=0.3, low_bars=48,
         drop_6h=0.015, atr_period=14, sl_atr_buf=2.5, tp_r=1.5,
         min_sl_pct=0.005, max_sl_pct=0.05)

GROUPS = json.load(open("/tmp/groups.json"))
GROUP_SYMS = {s for g, ss in GROUPS.items() if g != "majors" for s in ss}


def load(sym):
    rows = db.q(
        "SELECT ts,o,h,l,c,v FROM candles WHERE symbol=%s AND tf='1m' "
        "AND ts>=%s AND ts<%s ORDER BY ts",
        (sym, WIN_START - dt.timedelta(hours=WARMUP_H),
         WIN_END + dt.timedelta(minutes=FWD_MIN + 10)))
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df.astype(float)


def trunc_frames(d1, index):
    bucket = d1.index.floor("15min")
    minute = ((d1.index - bucket).total_seconds() // 60).astype(int)
    out = {}
    for m in DEPTHS:
        k = minute <= 14 - m
        g = d1[k].groupby(bucket[k])
        out[m] = pd.DataFrame({"h": g["h"].max(), "l": g["l"].min(),
                               "c": g["c"].last(), "v": g["v"].sum()}).reindex(index)
    return out


def clauses(F, m, C, atr_prev, avg_vol, lo12, c_ref):
    h, l, c, v = (F[m]["h"].values, F[m]["l"].values,
                  F[m]["c"].values, F[m]["v"].values)
    prev_c = C["c"].shift().values
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    atr = atr_prev * (1 - 1 / P["atr_period"]) + tr / P["atr_period"]
    with np.errstate(invalid="ignore"):
        r = dict(
            vol=v >= P["vol_mult"] * avg_vol,
            rng=(h - l) <= P["max_rng_atr"] * atr,
            low=l <= lo12 + P["low_prox_atr"] * atr,
            drop=(c / c_ref - 1) <= -P["drop_6h"])
        sl = np.minimum(l, lo12) - P["sl_atr_buf"] * atr
        dist = (c - sl) / c
        r["dist"] = (dist >= P["min_sl_pct"]) & (dist <= P["max_sl_pct"])
    ok = (atr > 0) & (avg_vol > 0) & np.isfinite(atr) & np.isfinite(c)
    r["fire"] = ok & r["vol"] & r["rng"] & r["low"] & r["drop"] & r["dist"]
    r["px"], r["sl"] = c, sl
    return r


def forward(d1v, d1i, t_ns, px, sl, tp):
    if not (np.isfinite(px) and np.isfinite(sl) and np.isfinite(tp)) or px <= sl:
        return None
    i0 = int(np.searchsorted(d1i, t_ns))
    i1 = min(i0 + FWD_MIN, len(d1i))
    if i1 - i0 < 30:
        return None
    h, l = d1v[i0:i1, 0], d1v[i0:i1, 1]
    raw = (d1v[i1 - 1, 2] / px - 1) * 100 - COST
    hs, ht = np.nonzero(l <= sl)[0], np.nonzero(h >= tp)[0]
    si = hs[0] if len(hs) else None
    ti = ht[0] if len(ht) else None
    if si is not None and (ti is None or si <= ti):
        stp = (sl / px - 1) * 100 - COST
    elif ti is not None:
        stp = (tp / px - 1) * 100 - COST
    else:
        stp = raw
    return raw, (((sl / px - 1) * 100 - COST) if si is not None else raw), stp


def stat(vals):
    v = np.array(vals, float)
    if not len(v):
        return "n=   0"
    w, lo = v[v > 0].sum(), -v[v < 0].sum()
    pf = (w / lo) if lo > 0 else float("inf")
    se = v.std(ddof=1) / np.sqrt(len(v)) if len(v) > 1 else float("nan")
    return (f"n={len(v):4d}  mean {v.mean():+7.3f}%  SE {se:5.3f}  "
            f"win% {100 * (v > 0).mean():5.1f}  PF {pf:6.3f}")


def main():
    syms = [r[0] for r in db.q(
        "SELECT DISTINCT symbol FROM candles WHERE tf='1m' ORDER BY symbol")]
    if os.environ.get("WEDGE_SYMS"):
        syms = os.environ["WEDGE_SYMS"].split(",")
    expected = (WIN_END - WIN_START).total_seconds() / 60

    # key -> dict(sym, depth_fire{m: (px, sl)}, lag{k: outcome}, spec_out)
    rec = {}
    attrib = dict(complete_only=Counter(), trunc_only=Counter())

    for sym in syms:
        d1 = load(sym)
        if d1 is None:
            continue
        if len(d1[(d1.index >= WIN_START) & (d1.index < WIN_END)]) < MIN_COV * expected:
            continue
        C = ind.resample(d1, "15min")
        n1m = d1["c"].resample("15min").count().reindex(C.index).fillna(0).astype(int)
        atr_prev = ind.atr(C, P["atr_period"]).shift().values
        avg_vol = C["v"].rolling(20).mean().shift().values
        lo12 = C["l"].rolling(P["low_bars"]).min().shift().values
        c_ref = C["c"].shift(24).values
        F = trunc_frames(d1, C.index)
        close_ts = C.index + dt.timedelta(minutes=15)
        base = np.zeros(len(C), bool)
        base[P["low_bars"] + 25:] = True
        base &= (close_ts >= WIN_START) & (close_ts <= WIN_END) & (n1m.values == 15)
        if not base.any():
            continue
        R = {m: clauses(F, m, C, atr_prev, avg_vol, lo12, c_ref) for m in DEPTHS}

        # clause attribution at m=1 (which clause explains each disagreement)
        for i in np.nonzero(base & R[0]["fire"] & ~R[1]["fire"])[0]:
            for cl in CLAUSES:
                if R[0][cl][i] and not R[1][cl][i]:
                    attrib["complete_only"][cl] += 1
        for i in np.nonzero(base & ~R[0]["fire"] & R[1]["fire"])[0]:
            for cl in CLAUSES:
                if R[1][cl][i] and not R[0][cl][i]:
                    attrib["trunc_only"][cl] += 1

        d1i, d1v = d1.index.asi8, np.column_stack(
            [d1["h"].values, d1["l"].values, d1["c"].values])
        any_fire = base & np.logical_or.reduce([R[m]["fire"] for m in DEPTHS])
        for i in np.nonzero(any_fire)[0]:
            t0 = (C.index[i] + dt.timedelta(minutes=15)).value
            e = dict(sym=sym, fires={}, lag={})
            for m in DEPTHS:
                if not R[m]["fire"][i]:
                    continue
                px, sl = float(R[m]["px"][i]), float(R[m]["sl"][i])
                o = forward(d1v, d1i, t0, px, sl, px + P["tp_r"] * (px - sl))
                if o:
                    e["fires"][m] = o
            if 0 in e["fires"]:                      # design (b): delayed entry
                sl = float(R[0]["sl"][i])
                for k in LAGS:
                    j = int(np.searchsorted(d1i, t0 + k * 60_000_000_000))
                    if j >= len(d1i) or d1i[j] != t0 + k * 60_000_000_000:
                        continue
                    px = float(d1v[j, 2])
                    o = forward(d1v, d1i, d1i[j], px, sl,
                                px + P["tp_r"] * (px - sl))
                    if o:
                        e["lag"][k] = o
            if e["fires"]:
                rec[(sym, str(C.index[i]))] = e

    # ---------------- report ----------------
    out = ["window_bar_close [%s .. %s]" % (WIN_START, WIN_END),
           f"signal-bar keys with at least one firing depth: {len(rec)}"]

    out.append("")
    out.append("=== CLAUSE ATTRIBUTION of the m=0 vs m=1 disagreement ===")
    out.append(f"  COMPLETE-ONLY (fires on complete bar, not at m=1): {dict(attrib['complete_only'])}")
    out.append(f"  TRUNC-ONLY    (fires at m=1, not on complete bar): {dict(attrib['trunc_only'])}")

    for uname, uni in (("ALL COLLECTED", None), ("GROUP SYMBOLS", GROUP_SYMS)):
        keys = [k for k, e in rec.items() if uni is None or e["sym"] in uni]
        spec = [k for k in keys if 0 in rec[k]["fires"]]
        out.append("")
        out.append(f"################ {uname} ################")
        out.append(f"SPEC (m=0, what the strategy's own rules license): {stat([rec[k]['fires'][0][2] for k in spec])}")

        for Rmax in (2, 3, 5):
            real = [k for k in keys if any(m in rec[k]["fires"] for m in range(Rmax + 1))]
            mstar = {k: max(m for m in rec[k]["fires"] if m <= Rmax) for k in real}
            extra = [k for k in real if 0 not in rec[k]["fires"]]
            out.append("")
            out.append(f"--- REALIZED(R={Rmax}): engine fires at m*=max(fires<=R) ---")
            out.append(f"  n_realized {len(real)}  n_spec {len(spec)}  "
                       f"EXTRA(engine takes, rules forbid) {len(extra)}  "
                       f"= +{len(extra) / len(spec) * 100:.1f}% of spec")
            out.append(f"  m* distribution {dict(sorted(Counter(mstar.values()).items()))}")
            for vi, vn in ((0, "raw"), (1, "stop"), (2, "stoptp")):
                s_spec = np.mean([rec[k]["fires"][0][vi] for k in spec])
                s_real = np.mean([rec[k]["fires"][mstar[k]][vi] for k in real])
                out.append(f"  {vn:6s} SPEC {stat([rec[k]['fires'][0][vi] for k in spec])}")
                out.append(f"  {vn:6s} REAL {stat([rec[k]['fires'][mstar[k]][vi] for k in real])}")
                out.append(f"  {vn:6s} WEDGE (REALIZED - SPEC) = {s_real - s_spec:+.3f} pp")
            out.append(f"  EXTRA  stoptp {stat([rec[k]['fires'][mstar[k]][2] for k in extra])}")
            if extra:
                c = Counter(rec[k]["sym"] for k in extra)
                t = c.most_common(5)
                out.append(f"  EXTRA  concentration: {len(c)} symbols; top5 "
                           f"{sum(n for _, n in t) / len(extra) * 100:.1f}%; largest {t[0]}")

        out.append("")
        out.append(f"--- DESIGN (b): SPEC set entered k minutes past the bar boundary ---")
        for k in [0] + LAGS:
            if k == 0:
                out.append(f"  lag  0 min  stoptp {stat([rec[q]['fires'][0][2] for q in spec])}")
            else:
                have = [q for q in spec if k in rec[q]["lag"]]
                out.append(f"  lag {k:2d} min  stoptp {stat([rec[q]['lag'][k][2] for q in have])}"
                           f"   (paired subset n={len(have)}, "
                           f"lag0 on same subset mean "
                           f"{np.mean([rec[q]['fires'][0][2] for q in have]):+.3f}%)")

    txt = "\n".join(out)
    print(txt)
    print("MD5", hashlib.md5(txt.encode()).hexdigest())


main()
