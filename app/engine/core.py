"""Shared decision loop used by BOTH the live engine and the backtester —
identical code path, so backtests and live paper trading can't drift apart."""
import datetime as dt
import logging

import pandas as pd

from ..common import indicators as ind
from .loader import TRADING_STATUSES

log = logging.getLogger("core")

FUNDING_HOURS = (0, 8, 16)  # UTC settlement times on MEXC


class Ctx:
    """What a strategy sees for one symbol at one moment."""

    def __init__(self, symbol: str, group: str, df1m: pd.DataFrame, regime: str,
                 funding_rate: float, now: dt.datetime):
        self.symbol = symbol
        self.group = group
        self.df = df1m            # 1m OHLCV, ascending ts index, up to `now`
        self.regime = regime
        self.funding = funding_rate or 0.0
        self.now = now
        self._tf_cache: dict[str, pd.DataFrame] = {}

    def tf(self, rule: str) -> pd.DataFrame:
        """Resampled view, e.g. ctx.tf('5min'), ctx.tf('15min'), ctx.tf('1h')."""
        if rule not in self._tf_cache:
            self._tf_cache[rule] = ind.resample(self.df, rule)
        return self._tf_cache[rule]

    @property
    def price(self) -> float:
        return float(self.df["c"].iloc[-1])


def step(*, strategies: list, broker, candles: dict[str, pd.DataFrame],
         groups: dict[str, str], regime: str, funding: dict[str, float],
         now: dt.datetime, cfg: dict, last_funding_settle: dt.datetime | None,
         on_event=None) -> dt.datetime | None:
    """One decision cycle. candles: symbol -> 1m df (history up to `now`).
    Returns the funding settlement boundary that has been applied (caller persists it).
    """
    emit = on_event or (lambda kind, msg: None)
    ctxs: dict[str, Ctx] = {}

    def ctx_for(symbol: str) -> Ctx | None:
        if symbol not in ctxs:
            df = candles.get(symbol)
            if df is None or len(df) < 60:
                return None
            ctxs[symbol] = Ctx(symbol, groups.get(symbol, "?"), df, regime,
                               funding.get(symbol, 0.0), now)
        return ctxs[symbol]

    # ---- funding settlement (00/08/16 UTC) ----
    settle = _last_settle(now)
    applied = None
    if last_funding_settle is None or settle > last_funding_settle:
        for pos in broker.open_positions():
            if pos["entry_ts"] <= settle:
                broker.apply_funding(pos, funding.get(pos["symbol"], 0.0))
        applied = settle

    # ---- manage open positions ----
    strat_by_name = {s.meta["name"]: s for s in strategies}
    for pos in broker.open_positions():
        c = ctx_for(pos["symbol"])
        if c is None:
            continue
        last = c.df.iloc[-1]
        if broker.check_sl_tp(pos, float(last["h"]), float(last["l"]), now):
            continue
        held_h = (now - pos["entry_ts"]).total_seconds() / 3600
        if held_h >= cfg["paper"]["max_hold_hours"]:
            broker.close(pos, c.price, now, "time-stop")
            continue
        strat = strat_by_name.get(pos["strategy"])
        if strat is not None:
            try:
                reason = strat.should_exit(c, pos)
            except Exception as e:  # noqa: BLE001
                log.exception("should_exit failed: %s", pos["strategy"])
                emit("error", f"{pos['strategy']}.should_exit crashed: {e}")
                reason = None
            if reason:
                broker.close(pos, c.price, now, reason)

    # ---- look for entries ----
    for strat in strategies:
        m = strat.meta
        if getattr(strat, "runtime_status", m.get("status")) not in TRADING_STATUSES:
            continue
        if regime not in m.get("regimes", []):
            continue
        for group in m.get("groups", []):
            for symbol in [s for s, g in groups.items() if g == group]:
                c = ctx_for(symbol)
                if c is None:
                    continue
                if not broker.can_open(m["name"], symbol, group, now):
                    continue
                try:
                    sig = strat.should_enter(c)
                except Exception as e:  # noqa: BLE001
                    log.exception("should_enter failed: %s", m["name"])
                    emit("error", f"{m['name']}.should_enter crashed: {e}")
                    continue
                if sig is None:
                    continue
                broker.open(strategy=m["name"], symbol=symbol, group=group,
                            side=sig.side, price=c.price, sl=sig.sl, tp=sig.tp,
                            regime=regime, ts=now, reason=sig.reason)
    return applied


def _last_settle(now: dt.datetime) -> dt.datetime:
    h = max(x for x in FUNDING_HOURS if x <= now.hour) if now.hour >= FUNDING_HOURS[0] \
        else FUNDING_HOURS[-1]
    day = now if now.hour >= FUNDING_HOURS[0] else now - dt.timedelta(days=1)
    return day.replace(hour=h, minute=0, second=0, microsecond=0)
