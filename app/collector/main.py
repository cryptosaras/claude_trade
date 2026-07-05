"""Collector: backfills and keeps 1m/1h candles fresh for the universe,
stores funding rates + tickers + open-interest history, and refreshes the
market regime."""
import datetime as dt
import logging
import time

import pandas as pd
from psycopg.types.json import Json

from ..common import db, regime
from ..common.config import load_settings, load_universe
from ..common.mexc import Mexc

log = logging.getLogger("collector")

TF_INTERVAL = {"1m": ("Min1", 60), "1h": ("Min60", 3600)}
CHUNK = 1900  # candles per kline request (MEXC returns up to ~2000)


def last_ts(symbol: str, tf: str):
    rows = db.q("SELECT max(ts) FROM candles WHERE symbol=%s AND tf=%s", (symbol, tf))
    return rows[0][0]


def upsert_candles(symbol: str, tf: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with db.pool().connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO candles (symbol, tf, ts, o, h, l, c, v) "
                "VALUES (%s, %s, to_timestamp(%s), %s, %s, %s, %s, %s) "
                "ON CONFLICT (symbol, tf, ts) DO UPDATE SET "
                "o=EXCLUDED.o, h=EXCLUDED.h, l=EXCLUDED.l, c=EXCLUDED.c, v=EXCLUDED.v",
                [(symbol, tf, r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows],
            )
    return len(rows)


def sync_symbol(mexc: Mexc, symbol: str, tf: str, backfill_days: int) -> int:
    interval, step = TF_INTERVAL[tf]
    now = int(time.time())
    latest = last_ts(symbol, tf)
    # re-fetch from the last stored bar (inclusive): it was stored while still
    # forming, and the upsert refreshes it with final o/h/l/c/v
    start = int(latest.timestamp()) if latest else now - backfill_days * 86400
    total = 0
    while start <= now:
        end = min(start + CHUNK * step, now)
        rows = mexc.klines(symbol, interval, start, end)
        total += upsert_candles(symbol, tf, rows)
        if not rows or rows[-1][0] <= start:
            break
        start = rows[-1][0] + step
    return total


def discover_symbols(tickers: list[dict], group_symbols: set[str], collect_cfg: dict) -> list[str]:
    """Group symbols are always collected. Everything else with 24h turnover
    >= min_turnover_usd is added too, ranked by turnover, up to max_symbols
    total — this is how new listings and pumping coins appear automatically."""
    min_turnover = collect_cfg["min_turnover_usd"]
    qualifying = sorted(
        (t for t in tickers if t.get("symbol", "").endswith("_USDT")
         and (t.get("lastPrice") or 0) > 0
         and (t.get("amount24") or 0) >= min_turnover
         and t["symbol"] not in group_symbols),
        key=lambda t: -(t.get("amount24") or 0),
    )
    ordered = list(group_symbols) + [t["symbol"] for t in qualifying]
    return ordered[:collect_cfg["max_symbols"]]


def sync_tickers(tickers: list[dict], symbols: set[str]) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    for t in tickers:
        if t.get("symbol") not in symbols:
            continue
        db.execute(
            "INSERT INTO tickers (symbol, price, change24h, turnover24h, funding_rate, updated) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (symbol) DO UPDATE SET "
            "price=EXCLUDED.price, change24h=EXCLUDED.change24h, "
            "turnover24h=EXCLUDED.turnover24h, funding_rate=EXCLUDED.funding_rate, "
            "updated=EXCLUDED.updated",
            (t["symbol"], t.get("lastPrice"), t.get("riseFallRate"),
             t.get("amount24"), t.get("fundingRate"), now),
        )
        db.execute(
            "INSERT INTO funding (symbol, ts, rate) VALUES (%s, %s, %s) "
            "ON CONFLICT (symbol, ts) DO NOTHING",
            (t["symbol"], now.replace(minute=0, second=0, microsecond=0),
             t.get("fundingRate")),
        )
        # open interest (contracts), one row per 5 minutes — OI surges and
        # divergences are strategy inputs once history accumulates
        db.execute(
            "INSERT INTO open_interest (symbol, ts, oi) VALUES (%s, %s, %s) "
            "ON CONFLICT (symbol, ts) DO NOTHING",
            (t["symbol"],
             now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0),
             t.get("holdVol")),
        )


def backfill_regime(cfg: dict) -> None:
    """One-time: compute historical hourly regimes from stored BTC 1h candles
    so backtests can split results by the regime that actually prevailed."""
    if db.kv_get("regime_backfilled"):
        return
    rows = db.qd("SELECT ts, o, h, l, c, v FROM candles "
                 "WHERE symbol='BTC_USDT' AND tf='1h' ORDER BY ts")
    rc = cfg["regime"]
    if len(rows) < rc["ema_slow"] + 40:
        return
    from ..common import indicators as ind
    df = pd.DataFrame(rows).set_index("ts")
    c = df["c"]
    ef, es = ind.ema(c, rc["ema_fast"]), ind.ema(c, rc["ema_slow"])
    a = ind.adx(df, rc["adx_period"])
    slope = es.pct_change(24)
    with db.pool().connection() as conn, conn.cursor() as cur:
        for i in range(rc["ema_slow"] + 24, len(df)):
            trending = a.iloc[i] >= rc["adx_trend_min"]
            if trending and c.iloc[i] > es.iloc[i] and ef.iloc[i] > es.iloc[i] \
                    and slope.iloc[i] > 0.001:
                label = "BULL"
            elif trending and c.iloc[i] < es.iloc[i] and ef.iloc[i] < es.iloc[i] \
                    and slope.iloc[i] < -0.001:
                label = "BEAR"
            else:
                label = "SIDE"
            cur.execute(
                "INSERT INTO regime (ts, label, confidence, meta) VALUES (%s, %s, 0.5, "
                "'{\"backfilled\": true}') ON CONFLICT (ts) DO NOTHING",
                # stamp at bar CLOSE: the label only becomes knowable then
                (df.index[i] + dt.timedelta(hours=1), label))
    db.kv_set("regime_backfilled", {"rows": len(df)})
    db.event("system", f"regime history backfilled ({len(df)} hours)")


def refresh_regime(cfg: dict) -> None:
    rows = db.qd(
        "SELECT ts, o, h, l, c, v FROM candles WHERE symbol='BTC_USDT' AND tf='1h' "
        "ORDER BY ts DESC LIMIT %s", (cfg["regime"]["ema_slow"] + 60,),
    )
    if not rows:
        return
    df = pd.DataFrame(rows[::-1]).set_index("ts")
    label, conf, meta = regime.detect(df, cfg["regime"])
    prev = db.q("SELECT label FROM regime ORDER BY ts DESC LIMIT 1")
    db.execute(
        "INSERT INTO regime (ts, label, confidence, meta) VALUES (now(), %s, %s, %s) "
        "ON CONFLICT (ts) DO NOTHING",
        (label, conf, Json(meta)),
    )
    if prev and prev[0][0] != label:
        db.event("regime", f"Regime change: {prev[0][0]} -> {label}", meta)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_settings()
    mexc = Mexc(cfg["mexc"]["base_url"], cfg["mexc"]["max_requests_per_sec"])
    db.wait_for_db()
    db.init_schema()
    db.event("system", "collector started")
    last_regime = 0.0
    while True:
        try:
            cfg = load_settings()  # re-read: config edits apply without restart
            uni = load_universe()  # re-read every cycle: AI may edit universe.yaml
            group_symbols = set(uni["symbols"])  # the actively-TRADED symbols
            tickers = mexc.tickers()
            # COLLECTED symbols: every group symbol plus anything liquid enough
            # to qualify (config/universe.yaml: collect.*) — auto-discovered,
            # no manual list. Trading only ever happens on group_symbols.
            symbols = discover_symbols(tickers, group_symbols, uni["collect"])
            for sym in symbols:
                n = sync_symbol(mexc, sym, "1m", cfg["mexc"]["backfill_days"])
                if n > 500:
                    log.info("backfilled %s 1m: %d candles", sym, n)
            # 1h only needed for BTC (regime) but cheap to keep for all
            for sym in symbols:
                sync_symbol(mexc, sym, "1h", cfg["mexc"]["backfill_days_1h"])
            sync_tickers(tickers, set(symbols))
            backfill_regime(cfg)
            if time.time() - last_regime > cfg["regime"]["refresh_seconds"]:
                refresh_regime(cfg)
                last_regime = time.time()
            db.kv_set("collector_heartbeat",
                      {"ts": time.time(), "symbols": len(symbols),
                       "trading_groups": len(group_symbols)})
        except Exception as e:  # noqa: BLE001
            log.exception("collector cycle failed")
            try:
                db.event("error", f"collector: {e}")
            except Exception:  # noqa: BLE001  (the DB itself may be down)
                pass
            time.sleep(10)
        time.sleep(15)


if __name__ == "__main__":
    run()
