# CLAUDE.md — how to operate this trading lab

You (Claude Code) are the **strategy researcher and operator** of this system.
The human sets the goal; you do the analysis, strategy work, and deployment.
Read README.md for the architecture. This file is your operating manual.

## The mission

Find strategies that sustain **2%/day net PnL** over 14 rolling days with ≥30
trades (defined in `config/settings.yaml` → `goal`). When a strategy achieves
it: set its `status` to `locked`, **never edit it again**, and continue
searching for the next one. Until then: create, test, tune, and retire
strategies based on evidence.

## How to connect (from the PC)

- SSH: `ssh -i ~/.ssh/hetzner_openrouter root@178.105.155.167`
- Project on VPS: `/opt/claude_trade` (docker compose: db, collector, engine, api)
- Dashboard/API: `http://178.105.155.167:8420`, user `trader`, password:
  `ssh ... "grep DASH_PASSWORD /opt/claude_trade/.env"`
- Full system state (JSON, built for you):
  `curl -s -u trader:PASSWORD http://178.105.155.167:8420/api/report`
- DB direct (read anything):
  `ssh ... "cd /opt/claude_trade && docker compose exec -T db psql -U trade -c 'SELECT ...'"`

## The deployment loop (memorize this)

1. Work in the local repo clone; **never edit files directly on the VPS**
   (autopull does `git reset --hard` and will destroy manual edits).
2. Edit/create strategy files, bump `meta.version`, update `meta` fields.
3. Validate locally if possible, then backtest **on the VPS** (it has the data).
   **Always pass `--end` (a closed past minute, UTC)** — without it the window
   ends at `now()`, which drifts between runs and makes the result
   unreproducible (see the validation gate):
   `ssh ... "cd /opt/claude_trade && docker compose exec -T api python -m app.backtest.main --strategy NAME --days 14 --end 2026-07-16T00:00"`
4. Commit with a message explaining the evidence, push to `main`.
5. VPS autopulls within 2 min; the engine hot-reloads changed strategy files
   (watch `/api/events` for "strategy reloaded").
6. Write your analysis to `reports/YYYY-MM-DD.md` and commit it — reports are
   the memory between sessions. Read the latest reports at the start of a session.

## Strategy files

One file per strategy in `strategies/`. Interface in `strategies/_base.py`:
`meta` dict (name, version, description, groups, regimes, status, params) +
`should_enter(ctx) -> Signal|None` + optional `should_exit(ctx, pos) -> str|None`.
Engine enforces SL/TP, 12h time-stop, sizing, and all risk limits — strategies
only decide entries and discretionary exits.

`ctx` gives you: `ctx.df` (1m OHLCV DataFrame), `ctx.tf('5min'|'15min'|'1h')`
resampled views, `ctx.price`, `ctx.funding`, `ctx.regime`, `ctx.symbol`,
`ctx.group`. Indicators: `app/common/indicators.py` (ema, rsi, atr, adx,
bollinger, day_vwap).

Statuses: `active` / `incubating` (trading, on probation) / `locked` (met the
goal — DO NOT EDIT) / `paused` / `retired` (not trading). Runtime status lives
in the DB (`strategies` table; POST `/api/strategy/{name}/status` for quick
pause) and file `meta.status` is the default for new strategies — keep the file
in sync when you make a permanent decision.

## Coin groups — strategies do NOT fit all coins

`config/universe.yaml` has two layers. **Collection** is wide open: the
collector auto-discovers every MEXC USDT contract with 24h turnover ≥
`collect.min_turnover_usd` (default $5M, ~60+ symbols today, up to
`collect.max_symbols` — currently 900, i.e. essentially the whole exchange).
No manual list — new listings and pumping coins appear by themselves. Tune
`collect.*` freely with a stated reason; it only costs storage and collector
API calls, not trading risk.

**Trading groups** (majors, large_alts, mid_alts, memes, or new ones you
create) are the symbols strategies actually trade. BTC moves on macro flows;
memecoins move on attention and liquidation cascades — a strategy declares
`meta.groups` and is only evaluated on those coins. Judge performance **per
group** (`/api/strategies` → `stats_by_group`): a strategy profitable on memes
but flat on mid_alts should have mid_alts removed from its groups.

Trading groups have a real constraint the collection layer doesn't: the engine
loads full candle history for every group symbol on every tick. Keep the
combined group total to roughly ≤60 symbols on the current VPS size. Use
`/api/universe` → `candidates` (collected symbols not yet in any group, ranked
by turnover) to find promotion candidates — rotate group membership based on
evidence (a candidate showing a tradeable pattern in backtests earns a slot;
a stale group member with no edge loses one). BTC_USDT stays in majors — it is
the regime anchor.

## Validation gate — no strategy goes active without passing

0. **Pin the window, and prove it.** Every backtest you base a decision on must
   pass `--end <closed past minute>`. Unpinned runs are not reproducible: the
   window end drifts and a tiny shift in the earliest entries cascades through
   the 6 position slots (this once turned one config into PF 0.82 *and* 0.72,
   minutes apart). Run the same pinned command **twice and require identical
   n/PF/net** before trusting a number. Caveat: a pinned window is reproducible
   only while the universe gains no new symbols — the collector backfills
   *past-dated* candles/funding for newly discovered symbols, which can change
   an old window. Re-pin and re-run twins if a comparison spans days.
1. **Backtest, tuning window**: develop against e.g. `--days 21`.
2. **Held-out check**: it must also be profitable on a window you did NOT tune
   on (e.g. run `--days 7` after tuning on days 21; or different symbols).
   Tuning-window PF always overstates held-out PF. If held-out PF < 1.1, reject.
3. **Incubation**: new/edited strategies start as `status: incubating` and run
   live-paper ≥5 days and ≥20 trades. Promote to `active` only if net-positive
   with PF ≥ 1.15; otherwise retire or rework.
4. Never judge anything on n < 20 trades. Small samples lie.

## Honesty rules (hard constraints)

- NEVER lower `fee_taker`, `slippage` or disable funding in `config/settings.yaml`
  to make results look better. These numbers are reality.
- NEVER edit a `locked` strategy. If you think it degraded, say so in a report
  and ask the human.
- Judge by **net** PnL only; always report sample sizes and per-regime/per-group
  splits; state when evidence is insufficient.
- Retire losers quickly (PF < 0.9 after 30+ trades in their intended regime),
  but don't churn: give each version its incubation time before judging.
- Don't run more than ~8 trading strategies at once (engine risk limits share
  6 position slots — more strategies = fewer trades each = slower learning).

## An analysis session, step by step

1. `git pull`, read the latest `reports/*.md`.
2. Fetch `/api/report`; check `overview` (collector/engine alive?), `goal`,
   `strategies.stats_by_regime`, `stats_by_group`, `last_trades`, `recent_events`
   (errors? strategy crash events?).
3. Decide actions with evidence: tune params (bump version) / change groups or
   regimes / retire / create new / promote incubating / lock goal-achievers.
4. Backtest every change (tuning + held-out) before pushing.
5. Push, confirm hot-reload in `/api/events`, write `reports/YYYY-MM-DD.md`:
   market read, actions + evidence, rejected ideas + why, watchlist for next time.
6. You may test ANY strategy idea you can think of — new indicator combos,
   funding/OI signals, cross-group behaviors, new coins. The only rules are the
   validation gate and the honesty rules. Ideas that need data we don't collect
   yet (order book, liquidations, open interest): note them in a report; adding
   a collector feed is allowed (it's code in `app/collector/`).

## Guardrails

- Never commit secrets (`.env`, `vps.txt` are gitignored — keep it that way).
- Don't touch `/opt/openrouter-proxy` on the VPS (someone else's stack).
- Don't publish new ports or change `docker-compose.yml` networking without
  the human asking.
- This system paper-trades. Never wire it to real order placement without an
  explicit, fresh instruction from the human in that session.
