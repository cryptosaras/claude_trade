"""The truncated-bar wedge census (pre-registered 2026-08-04).

The live engine treats a 15m bar as closed from its LABEL, not from how many 1m
candles it holds (whale_absorb._closed_bars). The collector refreshes a symbol
about every 87s while the engine ticks every 20s, so at the tick where bar T
first counts as closed, bar T is typically missing its last 1-2 minutes while
every EARLIER bar is long since complete.

This censuses whale_absorb's entry conjunction at truncation depth m (signal bar
rebuilt from its first 15-m 1m candles, all prior bars complete), for
m = 0 (the specified strategy) and m = 1..5, and measures the forward outcome of
every signal so the live-vs-backtest wedge can be priced.

ATR is RECOMPUTED on the truncated series -- ind.atr is a Wilder EMA, so with
only bar i modified: ATR_i(m) = ATR_{i-1}^complete * (13/14) + TR_i(m)/14, with
TR_i(m) built from the truncated high/low against the COMPLETE prior close.
(Using the complete-data ATR against a truncated range is the exact error that
forced the 2026-08-03 correction.)

No broker, no slots, no sizing, no leverage.
"""
import datetime as dt
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/srv")
from app.common import db  # noqa: E402
from app.common import indicators as ind  # noqa: E402

WIN_START = dt.datetime(2026, 7, 6, 0, 0, tzinfo=dt.timezone.utc)   # bar CLOSE >= this
WIN_END = dt.datetime(2026, 8, 3, 21, 0, tzinfo=dt.timezone.utc)    # bar CLOSE <= this
WARMUP_H = 30
FWD_MIN = 480            # 8h bounce window (max_hold_min)
COST = 0.16              # 0.10% fees round-trip + 0.06% slippage round-trip
MIN_COV = 0.90           # >=90% of expected 1m candles inside the window
DEPTHS = [0, 1, 2, 3, 4, 5]

P = dict(vol_mult=2.5, max_rng_atr=0.9, low_prox_atr=0.3, low_bars=48,
         drop_6h=0.015, atr_period=14, sl_atr_buf=2.5, tp_r=1.5,
         min_sl_pct=0.005, max_sl_pct=0.05)

GROUPS = json.load(open("/tmp/groups.json"))
GROUP_SYMS = {s for g, ss in GROUPS.items() if g != "majors" for s in ss}
SYM2GROUP = {s: g for g, ss in GROUPS.items() for s in ss}


def load(sym):
    rows = db.q(
        "SELECT ts,o,h,l,c,v FROM candles WHERE symbol=%s AND tf='1m' "
        "AND ts>=%s AND ts<%s ORDER BY ts",
        (sym, WIN_START - dt.timedelta(hours=WARMUP_H),
         WIN_END + dt.timedelta(minutes=FWD_MIN + 5)),
    )
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df.astype(float)


def trunc_frames(d1, index):
    """Per-15m-bucket OHLCV using only the first 15-m minutes, for each depth m."""
    bucket = d1.index.floor("15min")
    minute = ((d1.index - bucket).total_seconds() // 60).astype(int)
    out = {}
    for m in DEPTHS:
        sub = d1[minute <= 14 - m]
        g = sub.groupby(bucket[minute <= 14 - m])
        f = pd.DataFrame({"h": g["h"].max(), "l": g["l"].min(),
                          "c": g["c"].last(), "v": g["v"].sum()}).reindex(index)
        out[m] = f
    return out


def clauses(F, m, C, atr_prev, avg_vol, lo12, c_ref):
    """Vectorised entry conjunction at depth m. Returns dict of boolean arrays."""
    h, l, c, v = (F[m]["h"].values, F[m]["l"].values,
                  F[m]["c"].values, F[m]["v"].values)
    prev_c = C["c"].shift().values
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    atr = atr_prev * (1 - 1 / P["atr_period"]) + tr / P["atr_period"]
    with np.errstate(invalid="ignore"):
        c_vol = v >= P["vol_mult"] * avg_vol
        c_rng = (h - l) <= P["max_rng_atr"] * atr
        c_low = l <= lo12 + P["low_prox_atr"] * atr
        c_drop = (c / c_ref - 1) <= -P["drop_6h"]
        sl = np.minimum(l, lo12) - P["sl_atr_buf"] * atr
        dist = (c - sl) / c
        c_dist = (dist >= P["min_sl_pct"]) & (dist <= P["max_sl_pct"])
    ok = (atr > 0) & (avg_vol > 0) & np.isfinite(atr) & np.isfinite(c)
    fire = ok & c_vol & c_rng & c_low & c_drop & c_dist
    return dict(fire=fire, vol=c_vol, rng=c_rng, low=c_low, drop=c_drop,
                dist=c_dist, ok=ok, atr=atr, sl=sl, px=c)


def forward(d1v, d1i, t_open_ns, px, sl, tp):
    """1m forward path from the bar boundary. Returns (raw, stop, stoptp) net %."""
    if not (np.isfinite(px) and np.isfinite(sl) and np.isfinite(tp)) or px <= sl:
        return None
    i0 = int(np.searchsorted(d1i, t_open_ns))
    i1 = min(i0 + FWD_MIN, len(d1i))
    if i1 - i0 < 30:
        return None
    h = d1v[i0:i1, 0]
    l = d1v[i0:i1, 1]
    c_last = d1v[i1 - 1, 2]
    raw = (c_last / px - 1) * 100 - COST
    hit_sl = np.nonzero(l <= sl)[0]
    hit_tp = np.nonzero(h >= tp)[0]
    si = hit_sl[0] if len(hit_sl) else None
    ti = hit_tp[0] if len(hit_tp) else None
    stop = ((sl / px - 1) * 100 - COST) if si is not None else raw
    if si is not None and (ti is None or si <= ti):
        stoptp = (sl / px - 1) * 100 - COST
    elif ti is not None:
        stoptp = (tp / px - 1) * 100 - COST
    else:
        stoptp = raw
    return raw, stop, stoptp


def main():
    syms = [r[0] for r in db.q(
        "SELECT DISTINCT symbol FROM candles WHERE tf='1m' ORDER BY symbol")]
    if os.environ.get("WEDGE_SYMS"):        # smoke-test escape hatch only
        syms = os.environ["WEDGE_SYMS"].split(",")
    expected = (WIN_END - WIN_START).total_seconds() / 60

    sig = []            # per (symbol, bar, depth) firing record
    flip_any = 0        # power check: bars where any clause changes at m=1
    flip_by = dict(vol=0, rng=0, low=0, drop=0, dist=0)
    eval_bars = 0
    dropped_partial = 0
    atr_err = 0.0
    used = []

    for sym in syms:
        d1 = load(sym)
        if d1 is None:
            continue
        inwin = d1[(d1.index >= WIN_START) & (d1.index < WIN_END)]
        if len(inwin) < MIN_COV * expected:
            continue
        used.append(sym)

        C = ind.resample(d1, "15min")
        n1m = d1["c"].resample("15min").count().reindex(C.index).fillna(0).astype(int)
        atr_c = ind.atr(C, P["atr_period"])
        atr_prev = atr_c.shift().values
        avg_vol = C["v"].rolling(20).mean().shift().values
        lo12 = C["l"].rolling(P["low_bars"]).min().shift().values
        c_ref = C["c"].shift(24).values
        F = trunc_frames(d1, C.index)

        close_ts = C.index + dt.timedelta(minutes=15)
        need = P["low_bars"] + 25
        base = np.zeros(len(C), bool)
        base[need:] = True
        base &= (close_ts >= WIN_START) & (close_ts <= WIN_END)
        full = (n1m.values == 15)
        dropped_partial += int((base & ~full).sum())
        base &= full
        eval_bars += int(base.sum())
        if not base.any():
            continue

        R = {m: clauses(F, m, C, atr_prev, avg_vol, lo12, c_ref) for m in DEPTHS}

        d = R[1]
        z = R[0]
        ch = np.zeros(len(C), bool)
        for k in ("vol", "rng", "low", "drop", "dist"):
            f = base & (d[k] != z[k])
            flip_by[k] += int(f.sum())
            ch |= f
        flip_any += int(ch.sum())

        # instrument validation: at m=0 the recomputed ATR must equal ind.atr
        adiff = np.nanmax(np.abs((R[0]["atr"] - atr_c.values)[base])) if base.any() else 0.0
        atr_err = max(atr_err, float(adiff))

        d1i = d1.index.asi8
        d1v = np.column_stack([d1["h"].values, d1["l"].values, d1["c"].values])
        for m in DEPTHS:
            for i in np.nonzero(base & R[m]["fire"])[0]:
                px, sl = float(R[m]["px"][i]), float(R[m]["sl"][i])
                px0, sl0 = float(R[0]["px"][i]), float(R[0]["sl"][i])
                t_ns = (C.index[i] + dt.timedelta(minutes=15)).value
                w1 = forward(d1v, d1i, t_ns, px0, sl0,
                             px0 + P["tp_r"] * (px0 - sl0))
                w2 = forward(d1v, d1i, t_ns, px, sl,
                             px + P["tp_r"] * (px - sl))
                if w1 is None or w2 is None:
                    continue
                sig.append((sym, str(C.index[i]), m, px, sl, px0, sl0, i, w1, w2))

    # ---------- report ----------
    out = []
    out.append(f"window_bar_close [{WIN_START} .. {WIN_END}]")
    out.append(f"symbols_used {len(used)} of {len(syms)} collected  "
               f"(group symbols among them: {len(set(used) & GROUP_SYMS)})")
    out.append(f"evaluable_bars {eval_bars}  dropped_partial_1m_buckets {dropped_partial}")
    out.append(f"instrument_check max|ATR_recomputed(m=0) - ind.atr| = {atr_err:.3e} (must be ~0)")

    out.append("")
    out.append("=== POWER CHECK (base rate of clause flips at m=1, run before any null) ===")
    out.append(f"bars_with_ANY_clause_flip {flip_any}  of {eval_bars}  "
               f"rate {flip_any / eval_bars * 100:.4f}%")
    for k in ("vol", "rng", "low", "drop", "dist"):
        out.append(f"  flip_{k:5s} {flip_by[k]:8d}  rate {flip_by[k] / eval_bars * 100:.4f}%")

    def cell(rows, key, uni):
        v = np.array([r[key] for r in rows], float)
        if not len(v):
            return f"{uni:24s} n=0"
        w = v[v > 0].sum()
        lo = -v[v < 0].sum()
        pf = (w / lo) if lo > 0 else float("inf")
        se = v.std(ddof=1) / np.sqrt(len(v)) if len(v) > 1 else float("nan")
        return (f"{uni:24s} n={len(v):4d}  mean {v.mean():+7.3f}%  SE {se:5.3f}  "
                f"win% {100 * (v > 0).mean():5.1f}  PF {pf:5.3f}")

    def fired(m, universe=None):
        s = [r for r in sig if r[2] == m]
        if universe:
            s = [r for r in s if r[0] in universe]
        return {(r[0], r[1]) for r in s}

    def recs(keys, m, universe=None, chan=9):
        o = []
        for r in sig:
            if r[2] != m or (r[0], r[1]) not in keys:
                continue
            if universe and r[0] not in universe:
                continue
            o.append(dict(zip(("raw", "stop", "stoptp"), r[chan])))
        return o

    for uni_name, uni in (("ALL COLLECTED", None), ("GROUP SYMBOLS", GROUP_SYMS)):
        s0 = fired(0, uni)
        out.append("")
        out.append(f"=== {uni_name} ===")
        out.append(f"m=0 (SPECIFIED strategy, complete bars): {len(s0)} signals")
        for m in DEPTHS[1:]:
            sm = fired(m, uni)
            out.append(f"m={m}: {len(sm):4d} signals | TRUNC-ONLY {len(sm - s0):4d} | "
                       f"BOTH {len(sm & s0):4d} | COMPLETE-ONLY {len(s0 - sm):4d}")
        union = set().union(*[fired(m, uni) for m in DEPTHS[1:]])
        out.append(f"union m=1..5 (CEILING): {len(union)} signals | "
                   f"TRUNC-ONLY {len(union - s0)} | BOTH {len(union & s0)} | "
                   f"COMPLETE-ONLY {len(s0 - union)}")

        for m in (1, 2):
            sm = fired(m, uni)
            out.append("")
            out.append(f"--- {uni_name}: m={m} outcomes ---")
            for chan, lab in ((8, "W1 spec-priced"), (9, "W2 engine-priced")):
                for var in ("raw", "stop", "stoptp"):
                    a = cell(recs(s0, 0, uni, 8), var, f"[{lab}] m=0 SPEC {var}")
                    b = cell(recs(sm, m, uni, chan), var, f"[{lab}] m={m} ENGINE {var}")
                    out.append("  " + a)
                    out.append("  " + b)
                out.append("  " + "-" * 60)
            out.append(f"  TRUNC-ONLY  {cell(recs(sm - s0, m, uni, 9), 'stoptp', 'stoptp')}")
            out.append(f"  BOTH(eng)   {cell(recs(sm & s0, m, uni, 9), 'stoptp', 'stoptp')}")
            out.append(f"  COMPL-ONLY  {cell(recs(s0 - sm, 0, uni, 8), 'stoptp', 'stoptp')}")

    out.append("")
    out.append("=== VOID CONDITION (live entries the sl-channel already pinned) ===")
    for pid, sym, bar, want in ((526, "UNI_USDT", "2026-07-27 21:15:00+00:00", "depth 1-5"),
                                (530, "JTO_USDT", "2026-07-29 03:15:00+00:00", "depth 1-2"),
                                (527, "ORDI_USDT", "2026-07-28 17:15:00+00:00", "depth 1")):
        got = sorted(r[2] for r in sig if r[0] == sym and r[1] == bar)
        out.append(f"  {pid} {sym:11s} {bar[:16]} fires_at_depths {got}  (sl-channel says {want}, and NOT 0)")

    out.append("")
    out.append("=== CONCENTRATION (m=1, all collected, TRUNC-ONLY) ===")
    s0a, s1a = fired(0), fired(1)
    tos = [k[0] for k in (s1a - s0a)]
    from collections import Counter
    cnt = Counter(tos)
    tot = sum(cnt.values())
    if tot:
        top = cnt.most_common(10)
        out.append(f"  n={tot} over {len(cnt)} symbols; top10 {sum(c for _, c in top) / tot * 100:.1f}%; "
                   f"largest {top[0][0]} {top[0][1]} ({top[0][1] / tot * 100:.1f}%)")

    txt = "\n".join(out)
    print(txt)
    print("MD5", hashlib.md5(txt.encode()).hexdigest())


main()
