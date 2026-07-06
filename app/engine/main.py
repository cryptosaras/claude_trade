"""Live paper-trading engine: every tick, load fresh candles from the DB and
run the shared decision loop against the paper broker."""
import datetime as dt
import logging
import time

import pandas as pd

from ..common import db
from ..common.config import load_settings, load_universe
from . import core
from .account import Broker
from .loader import load_strategies

log = logging.getLogger("engine")


# Incremental candle cache. History only ever changes at the tail: the
# collector syncs forward from its last stored bar (inclusive), which is
# exactly the bar the engine cached last — so refetching a small overlap
# window past the cached tip sees every rewrite. Full-window queries for
# ~60 symbols every 20s tick moved ~57GB/13h between db and engine.
_cache: dict[tuple[str, str], pd.DataFrame] = {}
_cache_born = 0.0
CACHE_MAX_AGE = 1800  # full reload every 30 min: heals drift, applies config changes
_OVERLAP = {"1m": dt.timedelta(minutes=5), "1h": dt.timedelta(hours=2)}


def load_candles(symbols: list[str], lookback: int,
                 tf: str = "1m") -> dict[str, pd.DataFrame]:
    global _cache_born
    if time.time() - _cache_born > CACHE_MAX_AGE:
        _cache.clear()
        _cache_born = time.time()
    out = {}
    for sym in symbols:
        cached = _cache.get((sym, tf))
        if cached is None:
            rows = db.qd(
                "SELECT ts, o, h, l, c, v FROM candles WHERE symbol=%s AND tf=%s "
                "ORDER BY ts DESC LIMIT %s", (sym, tf, lookback))
            if not rows:
                continue
            df = pd.DataFrame(rows[::-1]).set_index("ts")
        else:
            since = (cached.index[-1] - _OVERLAP[tf]).to_pydatetime()
            rows = db.qd(
                "SELECT ts, o, h, l, c, v FROM candles WHERE symbol=%s AND tf=%s "
                "AND ts > %s ORDER BY ts", (sym, tf, since))
            if rows:
                fresh = pd.DataFrame(rows).set_index("ts")
                df = pd.concat(
                    [cached[cached.index < fresh.index[0]], fresh]).iloc[-lookback:]
            else:
                df = cached
        _cache[(sym, tf)] = df
        out[sym] = df
    return out


def current_regime() -> str:
    rows = db.q("SELECT label FROM regime ORDER BY ts DESC LIMIT 1")
    return rows[0][0] if rows else "SIDE"


def funding_rates() -> dict[str, float]:
    return {r[0]: float(r[1] or 0) for r in
            db.q("SELECT symbol, funding_rate FROM tickers")}


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    db.wait_for_db()
    db.init_schema()
    cfg = load_settings()
    broker = Broker(cfg, mode="db")
    db.event("system", f"engine started, equity {broker.equity:.2f}")
    last_snapshot = 0.0
    while True:
        t0 = time.time()
        try:
            cfg = load_settings()      # re-read every tick: config edits apply live
            broker.cfg = cfg["paper"]
            uni = load_universe()
            strategies = load_strategies(sync_db=True)
            now = dt.datetime.now(dt.timezone.utc)
            candles = load_candles(uni["symbols"], cfg["engine"]["candle_lookback"])
            candles_1h = load_candles(uni["symbols"], 450, tf="1h")
            lf = db.kv_get("last_funding_settle")
            last_settle = dt.datetime.fromisoformat(lf["ts"]) if lf else None
            applied = core.step(
                strategies=strategies, broker=broker, candles=candles,
                candles_1h=candles_1h, groups=uni["symbol_group"],
                regime=current_regime(), funding=funding_rates(), now=now,
                cfg=cfg, last_funding_settle=last_settle, bars_per_check=1,
                on_event=db.event)
            if applied:
                db.kv_set("last_funding_settle", {"ts": applied.isoformat()})
            if time.time() - last_snapshot > 60:
                db.execute(
                    "INSERT INTO equity (ts, equity) VALUES (now(), %s) "
                    "ON CONFLICT (ts) DO NOTHING", (broker.equity,))
                last_snapshot = time.time()
            db.kv_set("engine_heartbeat",
                      {"ts": time.time(), "strategies": len(strategies)})
        except Exception as e:  # noqa: BLE001
            log.exception("engine tick failed")
            try:
                db.event("error", f"engine: {e}")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(max(1.0, cfg["engine"]["tick_seconds"] - (time.time() - t0)))


if __name__ == "__main__":
    run()
