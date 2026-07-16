"""Backtester: replays stored 1m candles through the SAME core.step() the live
engine uses (memory broker instead of DB broker).

CLI:  python -m app.backtest.main --strategy range_fader --days 21
      python -m app.backtest.main --strategy all --days 30 --step-minutes 5
      python -m app.backtest.main --strategy X --days 14 --end 2026-07-16T00:00

Pass --end to pin the window: without it `end = now()` drifts between runs and
the result is not reproducible (a shift in the earliest entries cascades through
the 6 position slots). Any number used to make a decision needs a pinned --end
and two identical runs.
"""
import argparse
import datetime as dt
import json

import pandas as pd

from ..common import db
from ..common.config import load_settings, load_universe
from ..engine import core
from ..engine.account import Broker
from ..engine.loader import load_strategies


def load_history(symbols: list[str], start: dt.datetime, end: dt.datetime,
                 tf: str = "1m") -> dict[str, pd.DataFrame]:
    out = {}
    for sym in symbols:
        rows = db.qd(
            "SELECT ts, o, h, l, c, v FROM candles WHERE symbol=%s AND tf=%s "
            "AND ts >= %s AND ts <= %s ORDER BY ts", (sym, tf, start, end))
        if rows:
            out[sym] = pd.DataFrame(rows).set_index("ts")
    return out


def regime_series(start: dt.datetime,
                  end: dt.datetime) -> list[tuple[dt.datetime, str]]:
    rows = db.q("SELECT ts, label FROM regime WHERE ts >= %s AND ts <= %s "
                "ORDER BY ts", (start, end))
    return [(r[0], r[1]) for r in rows]


def regime_at(series, ts, default="SIDE") -> str:
    label = default
    for rts, rlabel in series:
        if rts > ts:
            break
        label = rlabel
    return label


def funding_series(symbols: list[str], start: dt.datetime,
                   end: dt.datetime) -> dict[str, list[tuple[dt.datetime, float]]]:
    """symbol -> ascending (ts, rate) from the hourly funding snapshots the
    collector stores — the rates that actually prevailed, not today's."""
    rows = db.q("SELECT symbol, ts, rate FROM funding WHERE symbol = ANY(%s) "
                "AND ts >= %s AND ts <= %s AND rate IS NOT NULL ORDER BY ts",
                (symbols, start, end))
    out: dict[str, list[tuple[dt.datetime, float]]] = {}
    for sym, ts, rate in rows:
        out.setdefault(sym, []).append((ts, float(rate)))
    return out


def funding_at(series: dict, idx: dict, ts: dt.datetime) -> dict[str, float]:
    """Newest stored rate at or before ts, per symbol. `idx` carries the moving
    pointers between calls; ts must be non-decreasing across calls."""
    rates = {}
    for sym, rows in series.items():
        i = idx.get(sym, -1)
        while i + 1 < len(rows) and rows[i + 1][0] <= ts:
            i += 1
        idx[sym] = i
        if i >= 0:
            rates[sym] = rows[i][1]
    return rates


def funding_asof(symbols: list[str], end: dt.datetime) -> dict[str, float]:
    """Newest stored rate at or before `end`, per symbol — the gap-filler for
    symbols/periods from before funding collection began. Read from the
    append-only funding table rather than the `tickers` snapshot, which the
    collector overwrites every cycle and which would make a run unreproducible.
    """
    rows = db.q("SELECT DISTINCT ON (symbol) symbol, rate FROM funding "
                "WHERE symbol = ANY(%s) AND ts <= %s AND rate IS NOT NULL "
                "ORDER BY symbol, ts DESC", (symbols, end))
    return {sym: float(rate) for sym, rate in rows}


def run_backtest(strategy_name: str, days: int, step_minutes: int = 5,
                 symbols: list[str] | None = None,
                 end: dt.datetime | None = None) -> dict:
    if step_minutes < 1:
        return {"error": "step_minutes must be >= 1"}
    cfg = load_settings()
    uni = load_universe()
    strategies = [s for s in load_strategies(sync_db=False)
                  if strategy_name in ("all", s.meta["name"])]
    if not strategies:
        return {"error": f"strategy '{strategy_name}' not found"}
    if strategy_name != "all":
        # explicit request overrides pause/retire; 'all' keeps file statuses so
        # retired strategies don't steal position slots from the live portfolio
        for s in strategies:
            s.runtime_status = "active"
    syms = symbols or uni["symbols"]
    # every DB read below is bounded by `end`, so a pinned --end makes the run
    # reproducible: the source tables are append-only, so bounding is equivalent
    # to snapshotting them. Without it, `end = now()` drifts between runs and a
    # tiny shift in the earliest entries cascades through the 6 position slots.
    end = end or dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days)
    # full-universe history: --symbols restricts what may be TRADED, not what a
    # strategy SEES — cross-symbol signals (group baskets, ctx.btc) must be
    # computed from the same symbols live and in symbol-split holdouts
    history = load_history(uni["symbols"],
                           start - dt.timedelta(minutes=cfg["engine"]["candle_lookback"]),
                           end)
    history_1h = load_history(uni["symbols"], start - dt.timedelta(days=30), end,
                              tf="1h")
    if not history:
        return {"error": "no candle history in DB for the requested window"}
    regimes = regime_series(start - dt.timedelta(days=2), end)
    # historical funding, as-of each step; the as-of-end snapshot only fills
    # gaps (symbols/periods from before funding collection began)
    funding_hist = funding_series(syms, start - dt.timedelta(days=2), end)
    funding_snap = funding_asof(syms, end)
    funding_idx: dict[str, int] = {}

    broker = Broker(cfg, mode="mem")
    lookback = cfg["engine"]["candle_lookback"]
    t = start.replace(second=0, microsecond=0)
    last_settle = None
    step_delta = dt.timedelta(minutes=step_minutes)
    while t < end:
        window = {}
        window_1h = {}
        for sym, df in history.items():
            # side="left" excludes the bar stamped `t` (it spans [t, t+1m) — the
            # future). Only bars fully closed by `t` are visible, like live.
            pos = df.index.searchsorted(t, side="left")
            if pos >= 60:
                window[sym] = df.iloc[max(0, pos - lookback):pos]
        for sym, df in history_1h.items():
            pos = df.index.searchsorted(t - dt.timedelta(hours=1), side="left")
            if pos >= 10:
                window_1h[sym] = df.iloc[max(0, pos - 500):pos]
        if window:
            funding = {**funding_snap, **funding_at(funding_hist, funding_idx, t)}
            applied = core.step(
                strategies=strategies, broker=broker, candles=window,
                candles_1h=window_1h, groups=uni["symbol_group"],
                regime=regime_at(regimes, t), funding=funding, now=t, cfg=cfg,
                last_funding_settle=last_settle, bars_per_check=step_minutes,
                tradable=set(syms))
            if applied:
                last_settle = applied
        t += step_delta
    # close whatever is still open at the end, at last price
    for pos in list(broker.open_positions()):
        df = history.get(pos["symbol"])
        if df is not None and len(df):
            broker.close(pos, float(df["c"].iloc[-1]), end, "backtest-end")
    return summarize(broker, cfg, days, strategy_name)


def summarize(broker: Broker, cfg: dict, days: int, strategy_name: str) -> dict:
    trades = broker.closed
    if not trades:
        return {"strategy": strategy_name, "days": days, "trades": 0,
                "note": "no trades generated"}
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    start_eq = cfg["paper"]["start_equity"]
    # max drawdown over the equity path
    eq, peak, max_dd = start_eq, start_eq, 0.0
    for tr in sorted(trades, key=lambda x: x["exit_ts"]):
        eq += tr["net_pnl"]
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)

    def bucket(key):
        out = {}
        for tr in trades:
            out.setdefault(tr[key], []).append(tr["net_pnl"])
        return {k: {"trades": len(v), "net": round(sum(v), 2),
                    "pf": _pf(v)} for k, v in out.items()}

    return {
        "strategy": strategy_name, "days": days,
        "trades": len(trades), "win_rate": round(100 * len(wins) / len(trades), 1),
        "net_pnl": round(sum(t["net_pnl"] for t in trades), 2),
        "net_return_pct": round(100 * (broker.equity / start_eq - 1), 2),
        "avg_daily_pct": round(100 * (broker.equity / start_eq - 1) / max(days, 1), 3),
        "profit_factor": _pf([t["net_pnl"] for t in trades]),
        "max_drawdown_pct": round(100 * max_dd, 2),
        "avg_hold_hours": round(sum((t["exit_ts"] - t["entry_ts"]).total_seconds()
                                    for t in trades) / len(trades) / 3600, 2),
        "fees_paid": round(sum(t["fees"] for t in trades), 2),
        "funding_paid": round(sum(t["funding_paid"] for t in trades), 2),
        "by_regime": bucket("regime_entry"),
        "by_group": bucket("grp"),
        "by_strategy": bucket("strategy"),
    }


def _pf(pnls: list[float]):
    w = sum(p for p in pnls if p > 0)
    l = abs(sum(p for p in pnls if p <= 0))
    return round(w / l, 2) if l > 0 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, help="strategy name or 'all'")
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--step-minutes", type=int, default=5)
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--end", default=None,
                    help="pin the window end, ISO8601 UTC (e.g. 2026-07-16T00:00). "
                         "Defaults to now, which is NOT reproducible — pass a "
                         "closed past minute whenever a number must be trusted.")
    args = ap.parse_args()
    end = None
    if args.end:
        end = dt.datetime.fromisoformat(args.end)
        if end.tzinfo is None:
            end = end.replace(tzinfo=dt.timezone.utc)
    db.wait_for_db()
    result = run_backtest(args.strategy, args.days, args.step_minutes, args.symbols,
                          end)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
