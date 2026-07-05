"""Dashboard + control API. HTTP Basic auth (user: trader / DASH_PASSWORD env)."""
import datetime as dt
import os
import secrets
import threading
import time

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Json
from pydantic import BaseModel

from ..backtest.main import run_backtest
from ..common import db
from ..common.config import ROOT, load_settings, load_universe

app = FastAPI(title="claude_trade")
security = HTTPBasic()

VALID_STATUSES = {"active", "incubating", "locked", "paused", "retired"}


def auth(cred: HTTPBasicCredentials = Depends(security)) -> str:
    ok_user = secrets.compare_digest(cred.username, "trader")
    ok_pass = secrets.compare_digest(cred.password, os.environ.get("DASH_PASSWORD", ""))
    if not (ok_user and ok_pass):
        raise HTTPException(401, "bad credentials",
                            headers={"WWW-Authenticate": "Basic"})
    return cred.username


@app.on_event("startup")
def startup() -> None:
    db.wait_for_db()
    db.init_schema()


@app.get("/")
def index():
    # unauthenticated: serves only the static shell; every /api/* call needs auth,
    # and the page shows its own login overlay on 401
    return FileResponse(ROOT / "ui" / "index.html")


@app.get("/api/overview", dependencies=[Depends(auth)])
def overview():
    cfg = load_settings()
    start_eq = cfg["paper"]["start_equity"]
    realized = db.q("SELECT coalesce(sum(net_pnl),0) FROM positions "
                    "WHERE status='closed' AND mode='live'")
    equity = start_eq + float(realized[0][0])
    regime = db.qd("SELECT ts, label, confidence, meta FROM regime ORDER BY ts DESC LIMIT 1")
    today = db.q(
        "SELECT coalesce(sum(net_pnl),0), count(*) FROM positions "
        "WHERE status='closed' AND mode='live' "
        "AND (exit_ts AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date")
    open_pos = db.q("SELECT count(*) FROM positions WHERE status='open' AND mode='live'")
    col_hb = db.kv_get("collector_heartbeat") or {}
    eng_hb = db.kv_get("engine_heartbeat") or {}
    now = time.time()
    return {
        "equity": round(equity, 2),
        "return_pct": round(100 * (equity / start_eq - 1), 2),
        "today_pnl": round(float(today[0][0]), 2),
        "today_trades": today[0][1],
        "open_positions": open_pos[0][0],
        "regime": regime[0] if regime else None,
        "collector_alive": now - col_hb.get("ts", 0) < 120,
        "engine_alive": now - eng_hb.get("ts", 0) < 120,
        "goal": goal_progress(),
        "server_time": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


@app.get("/api/strategies", dependencies=[Depends(auth)])
def strategies():
    rows = db.qd("SELECT name, status, meta, updated FROM strategies ORDER BY name")
    stats = db.qd("""
        SELECT strategy, regime_entry AS regime, count(*) AS trades,
               round(sum(net_pnl)::numeric, 2) AS net,
               round((100.0 * count(*) FILTER (WHERE net_pnl > 0) / count(*))::numeric, 1) AS win_rate,
               round(avg(EXTRACT(epoch FROM exit_ts - entry_ts) / 3600)::numeric, 2) AS avg_hold_h,
               round((sum(net_pnl) FILTER (WHERE net_pnl > 0) /
                 nullif(abs(sum(net_pnl) FILTER (WHERE net_pnl <= 0)), 0))::numeric, 2) AS pf
        FROM positions WHERE status='closed' AND mode='live'
        GROUP BY strategy, regime_entry""")
    by_group = db.qd("""
        SELECT strategy, grp, count(*) AS trades,
               round(sum(net_pnl)::numeric, 2) AS net,
               round((sum(net_pnl) FILTER (WHERE net_pnl > 0) /
                 nullif(abs(sum(net_pnl) FILTER (WHERE net_pnl <= 0)), 0))::numeric, 2) AS pf
        FROM positions WHERE status='closed' AND mode='live'
        GROUP BY strategy, grp""")
    daily = db.qd("""
        SELECT strategy, exit_ts::date AS day, sum(net_pnl) AS net
        FROM positions WHERE status='closed' AND mode='live'
          AND exit_ts > now() - interval '14 days'
        GROUP BY strategy, day""")
    return {"strategies": rows, "stats_by_regime": stats,
            "stats_by_group": by_group, "daily_pnl_14d": daily}


@app.get("/api/positions", dependencies=[Depends(auth)])
def positions():
    return db.qd(
        "SELECT p.*, t.price AS mark FROM positions p "
        "LEFT JOIN tickers t ON t.symbol = p.symbol "
        "WHERE p.status='open' AND p.mode='live' ORDER BY p.entry_ts DESC")


@app.get("/api/trades", dependencies=[Depends(auth)])
def trades(limit: int = 100):
    return db.qd(
        "SELECT * FROM positions WHERE status='closed' AND mode='live' "
        "ORDER BY exit_ts DESC LIMIT %s", (min(limit, 1000),))


@app.get("/api/candles", dependencies=[Depends(auth)])
def candles(symbol: str = "BTC_USDT", tf: str = "1m", limit: int = 500):
    rows = db.qd(
        "SELECT extract(epoch FROM ts)::bigint AS t, o, h, l, c, v FROM candles "
        "WHERE symbol=%s AND tf=%s ORDER BY ts DESC LIMIT %s",
        (symbol, tf if tf in ("1m", "1h") else "1m", min(limit, 3000)))
    return rows[::-1]


@app.get("/api/markers", dependencies=[Depends(auth)])
def markers(symbol: str = "BTC_USDT", hours: int = 48):
    return db.qd(
        "SELECT id, strategy, side, entry_price, extract(epoch FROM entry_ts)::bigint AS entry_t, "
        "exit_price, extract(epoch FROM exit_ts)::bigint AS exit_t, net_pnl, status, sl, tp "
        "FROM positions WHERE symbol=%s AND mode='live' AND entry_ts > now() - make_interval(hours => %s)",
        (symbol, hours))


@app.get("/api/regime", dependencies=[Depends(auth)])
def regime_history(days: int = 30):
    return db.qd(
        "SELECT ts, label, confidence FROM regime "
        "WHERE ts > now() - make_interval(days => %s) ORDER BY ts", (days,))


@app.get("/api/equity", dependencies=[Depends(auth)])
def equity_history(days: int = 60):
    return db.qd(
        "SELECT extract(epoch FROM ts)::bigint AS t, equity FROM equity "
        "WHERE ts > now() - make_interval(days => %s) ORDER BY ts", (days,))


@app.get("/api/events", dependencies=[Depends(auth)])
def events(limit: int = 60):
    return db.qd("SELECT ts, kind, message FROM events ORDER BY id DESC LIMIT %s",
                 (min(limit, 500),))


@app.get("/api/universe", dependencies=[Depends(auth)])
def universe():
    """groups/tickers: the actively-traded universe (what the dashboard charts).
    candidates: collected-but-not-traded symbols, ranked by turnover — the pool
    the AI rotates new group members in from (see CLAUDE.md)."""
    uni = load_universe()
    tickers = db.qd("SELECT * FROM tickers ORDER BY turnover24h DESC NULLS LAST")
    known = set(uni["symbols"])
    return {"groups": uni["groups"],
            "collect": uni["collect"],
            "tickers": [t for t in tickers if t["symbol"] in known],
            "candidates": [t for t in tickers if t["symbol"] not in known][:80],
            "collected_count": len(tickers)}


def goal_progress():
    """Per-strategy rolling daily PnL% vs the 2%/day goal."""
    cfg = load_settings()["goal"]
    start_eq = load_settings()["paper"]["start_equity"]
    rows = db.qd("""
        SELECT strategy, count(*) AS trades, sum(net_pnl) AS net,
               count(DISTINCT exit_ts::date) AS days_traded
        FROM positions WHERE status='closed' AND mode='live'
          AND exit_ts > now() - make_interval(days => %s)
        GROUP BY strategy""", (cfg["rolling_days"],))
    out = []
    for r in rows:
        daily_pct = 100 * float(r["net"]) / start_eq / cfg["rolling_days"]
        out.append({
            "strategy": r["strategy"], "trades": r["trades"],
            "avg_daily_pct": round(daily_pct, 3),
            "target_daily_pct": cfg["daily_pnl_pct"],
            "achieved": daily_pct >= cfg["daily_pnl_pct"] and r["trades"] >= cfg["min_trades"],
        })
    return sorted(out, key=lambda x: -x["avg_daily_pct"])


@app.get("/api/report", dependencies=[Depends(auth)])
def report():
    """Condensed machine-readable state for the AI analyst."""
    return {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
        "overview": overview(),
        "strategies": strategies(),
        "goal": goal_progress(),
        "open_positions": positions(),
        "last_trades": trades(60),
        "recent_events": events(40),
        "universe": {g: s for g, s in load_universe()["groups"].items()},
        "settings": load_settings(),
    }


class StatusBody(BaseModel):
    status: str


@app.post("/api/strategy/{name}/status", dependencies=[Depends(auth)])
def set_status(name: str, body: StatusBody):
    if body.status not in VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(VALID_STATUSES)}")
    if not db.q("SELECT 1 FROM strategies WHERE name=%s", (name,)):
        raise HTTPException(404, "unknown strategy")
    db.execute("UPDATE strategies SET status=%s, updated=now() WHERE name=%s",
               (body.status, name))
    db.event("system", f"strategy {name} status -> {body.status} (via API)")
    return {"ok": True, "name": name, "status": body.status}


class BacktestBody(BaseModel):
    strategy: str
    days: int = 21
    step_minutes: int = 5


@app.post("/api/backtest", dependencies=[Depends(auth)])
def backtest(body: BacktestBody):
    rows = db.q(
        "INSERT INTO backtests (params) VALUES (%s) RETURNING id",
        (Json(body.model_dump()),))
    bt_id = rows[0][0]

    def work():
        try:
            result = run_backtest(body.strategy, body.days, body.step_minutes)
            db.execute("UPDATE backtests SET status='done', result=%s WHERE id=%s",
                       (Json(result, dumps=_json_dumps), bt_id))
        except Exception as e:  # noqa: BLE001
            db.execute("UPDATE backtests SET status='failed', result=%s WHERE id=%s",
                       (Json({"error": str(e)}), bt_id))

    threading.Thread(target=work, daemon=True).start()
    return {"id": bt_id, "status": "running"}


@app.get("/api/backtest/{bt_id}", dependencies=[Depends(auth)])
def backtest_result(bt_id: int):
    rows = db.qd("SELECT * FROM backtests WHERE id=%s", (bt_id,))
    if not rows:
        raise HTTPException(404, "no such backtest")
    return rows[0]


@app.get("/api/backtests", dependencies=[Depends(auth)])
def backtests(limit: int = 20):
    return db.qd("SELECT * FROM backtests ORDER BY id DESC LIMIT %s", (limit,))


def _json_dumps(obj):
    import json
    return json.dumps(obj, default=str)


# static assets (chart library); data still lives behind /api/* auth
app.mount("/static", StaticFiles(directory=ROOT / "ui"), name="static")
