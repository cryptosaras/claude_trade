# claude_trade — MEXC futures paper-trading lab with an AI strategy loop

A self-hosted system that collects live MEXC futures market data, paper-trades
Python strategy files in real time with honest costs (fees, slippage, funding),
shows everything in a web dashboard, and is designed to be **operated by Claude
Code**: the AI reads performance data, then creates/edits/retires strategies
through git. Everything runs on the VPS; your PC is the control seat.

**The goal:** find at least one strategy that sustains **2%/day net PnL**
(measured over 14 rolling days, minimum 30 trades). When a strategy achieves it,
it gets status `locked` — it keeps trading but is never edited — and the search
continues for the next one.

## Architecture

```
                    VPS (Hetzner, /opt/claude_trade)
 ┌──────────────────────────────────────────────────────────────┐
 │  docker compose:                                             │
 │   db         TimescaleDB (candles, trades, equity, events)   │
 │   collector  polls MEXC futures: 1m+1h candles, funding,     │
 │              tickers · detects market regime (BULL/BEAR/SIDE)│
 │   engine     every 20s: runs strategy files against fresh    │
 │              candles, manages paper positions (fees, slippage│
 │              funding, SL/TP, time-stop, risk limits)         │
 │   api        FastAPI :8420 — dashboard UI + JSON + controls  │
 │                                                              │
 │  cron ops/autopull.sh (every 2 min): git pull → hot-apply    │
 └──────────────────────────────────────────────────────────────┘
        ▲ git push                          ▲ https://IP:8420
        │                                   │ (user: trader)
 ┌──────┴───────────────┐          ┌────────┴──────┐
 │ Your PC: Claude Code │          │ Browser (you) │
 │ edits strategies,    │          └───────────────┘
 │ reads /api/report,   │
 │ runs backtests (ssh) │
 └──────────────────────┘
   GitHub: cryptosaras/claude_trade (source of truth)
```

## Where things are

| What | Where |
|---|---|
| Dashboard | `http://178.105.155.167:8420` — user `trader`, password in `/opt/claude_trade/.env` on the VPS |
| VPS project dir | `/opt/claude_trade` (does not touch the existing `/opt/openrouter-proxy` stack) |
| SSH | `ssh -i ~/.ssh/hetzner_openrouter root@178.105.155.167` |
| Strategies | `strategies/*.py` — one file per strategy |
| Coin groups | `config/universe.yaml` — majors / large_alts / mid_alts / memes |
| System settings | `config/settings.yaml` — fees, risk, goal definition |
| AI instructions | `CLAUDE.md` — how Claude Code operates this system |
| AI reports | `reports/` — committed analysis reports |

## How it works

1. **Collector** backfills 30 days of 1m candles (plus 180d of 1h) for every
   symbol in `config/universe.yaml`, then keeps them fresh (~15s cycle), stores
   funding rates and 24h tickers, and refreshes the market regime every 5 min
   (BTC 1h EMA-200 slope + ADX: BULL / BEAR / SIDE).
2. **Engine** hot-loads `strategies/*.py` every tick. A strategy declares which
   **coin groups** and which **regimes** it trades — BTC does not behave like a
   20M-mcap memecoin, so strategies never apply to everything blindly. When a
   strategy signals, the engine opens a **paper position** sized at 1% equity
   risk, max 3x leverage, and charges taker fees (0.05%/side), slippage (0.03%)
   and real funding every 8h. Exits: SL/TP (intrabar, SL-first when ambiguous),
   strategy exit logic, and a hard 12h time-stop (the 1–12h mandate).
3. **Backtester** replays stored candles through the *same* decision code —
   `python -m app.backtest.main --strategy range_fader --days 21` or the
   Backtest tab / `POST /api/backtest`.
4. **Dashboard** (port 8420) shows live candles with trade markers, positions,
   trades (gross vs costs vs net), per-regime and per-group performance,
   regime ribbon, equity curve, event feed, goal progress, and backtests.
5. **The AI loop**: a Claude Code session (on your PC, or scheduled) reads
   `/api/report`, decides what to change, edits strategy files, backtests,
   commits and pushes. The VPS autopulls within 2 minutes; the engine hot-reloads
   changed strategies without restart. Rules in `CLAUDE.md`.

## Operating it

```bash
# see logs
ssh -i ~/.ssh/hetzner_openrouter root@178.105.155.167 \
  "cd /opt/claude_trade && docker compose logs --tail 50 engine collector api"

# restart everything
ssh ... "cd /opt/claude_trade && docker compose restart"

# run a backtest on the VPS
ssh ... "cd /opt/claude_trade && docker compose exec -T api python -m app.backtest.main --strategy all --days 14"

# get the dashboard password
ssh ... "grep DASH_PASSWORD /opt/claude_trade/.env"

# reset the paper account (archives nothing — trades stay in DB, equity resets)
ssh ... "cd /opt/claude_trade && docker compose exec -T db psql -U trade -c \"UPDATE kv SET value='{\\\"value\\\": 10000}' WHERE key='paper_equity'\""
```

Deployment flow after any code/strategy change: **commit → push to `main` →
wait ≤2 min** (`ops/autopull.sh` runs from cron). Strategy/config/UI changes
apply instantly via bind mounts + hot reload; `app/` changes trigger a container
rebuild automatically.

## Honesty rules (why numbers can be trusted)

- Fees, slippage and funding are always charged; the backtester and live engine
  share one code path so they cannot drift apart.
- Intrabar SL/TP ambiguity resolves to the stop-loss (pessimistic).
- Performance is always reported net, split by regime and by coin group, with
  sample sizes.
- Expect most strategies to lose. That is the system working — it measures
  honestly so the AI can kill losers fast and iterate.

## Costs & limits

- VPS: existing Hetzner box (2 vCPU / 4GB / shared with openrouter-proxy).
- MEXC market data is public — no API key needed for paper trading.
- MEXC restricts *real* futures order placement via API; when a strategy proves
  itself and you want live execution, that's a separate decision (other
  exchange, or manual execution) — the strategy logic transfers.
