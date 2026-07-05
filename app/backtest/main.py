"""Backtester: replays stored 1m candles through the SAME core.step() the live
engine uses (memory broker instead of DB broker).

CLI:  python -m app.backtest.main --strategy range_fader --days 21
      python -m app.backtest.main --strategy all --days 30 --step-minutes 5
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


def load_history(symbols: list[str], start: dt.datetime) -> dict[str, pd.DataFrame]:
    out = {}
    for sym in symbols:
        rows = db.qd(
            "SELECT ts, o, h, l, c, v FROM candles WHERE symbol=%s AND tf='1m' "
            "AND ts >= %s ORDER BY ts", (sym, start))
        if rows:
            out[sym] = pd.DataFrame(rows).set_index("ts")
    return out


def regime_series(start: dt.datetime) -> list[tuple[dt.datetime, str]]:
    rows = db.q("SELECT ts, label FROM regime WHERE ts >= %s ORDER BY ts", (start,))
    return [(r[0], r[1]) for r in rows]


def regime_at(series, ts, default="SIDE") -> str:
    label = default
    for rts, rlabel in series:
        if rts > ts:
            break
        label = rlabel
    return label


def run_backtest(strategy_name: str, days: int, step_minutes: int = 5,
                 symbols: list[str] | None = None) -> dict:
    cfg = load_settings()
    uni = load_universe()
    strategies = [s for s in load_strategies(sync_db=False)
                  if strategy_name in ("all", s.meta["name"])]
    if not strategies:
        return {"error": f"strategy '{strategy_name}' not found"}
    for s in strategies:
        s.runtime_status = "active"  # backtest ignores pause/retire status
    syms = symbols or uni["symbols"]
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    history = load_history(syms, start - dt.timedelta(minutes=cfg["engine"]["candle_lookback"]))
    if not history:
        return {"error": "no candle history in DB for the requested window"}
    regimes = regime_series(start - dt.timedelta(days=2))
    funding = {r[0]: float(r[1] or 0) for r in
               db.q("SELECT symbol, funding_rate FROM tickers")}

    broker = Broker(cfg, mode="mem")
    lookback = cfg["engine"]["candle_lookback"]
    t = start.replace(second=0, microsecond=0)
    end = dt.datetime.now(dt.timezone.utc)
    last_settle = None
    step_delta = dt.timedelta(minutes=step_minutes)
    while t < end:
        window = {}
        for sym, df in history.items():
            pos = df.index.searchsorted(t, side="right")
            if pos >= 60:
                window[sym] = df.iloc[max(0, pos - lookback):pos]
        if window:
            applied = core.step(
                strategies=strategies, broker=broker, candles=window,
                groups=uni["symbol_group"], regime=regime_at(regimes, t),
                funding=funding, now=t, cfg=cfg, last_funding_settle=last_settle)
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
    args = ap.parse_args()
    db.wait_for_db()
    result = run_backtest(args.strategy, args.days, args.step_minutes, args.symbols)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
