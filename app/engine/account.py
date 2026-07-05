"""Paper broker: position accounting with fees, slippage and funding.
One implementation, two storage modes — 'db' (live) and 'mem' (backtest) — so
live paper trading and backtesting share the exact same money math.

Equity is always derived: start_equity + SUM(net_pnl of closed positions).
Nothing is stored that could drift from the trade log."""
import datetime as dt

from ..common import db


class Broker:
    def __init__(self, cfg: dict, mode: str = "db", start_equity: float | None = None):
        self.cfg = cfg["paper"]
        self.mode = mode
        if mode == "db":
            row = db.q("SELECT coalesce(sum(net_pnl),0) FROM positions "
                       "WHERE status='closed' AND mode='live'")
            self.equity = self.cfg["start_equity"] + float(row[0][0])
        else:
            self.equity = start_equity or self.cfg["start_equity"]
            self.positions: list[dict] = []
            self.closed: list[dict] = []

    # ---------- queries ----------
    def open_positions(self) -> list[dict]:
        if self.mode == "db":
            return db.qd("SELECT * FROM positions WHERE status='open' AND mode='live'")
        return [p for p in self.positions if p["status"] == "open"]

    def realized_today(self, now: dt.datetime) -> float:
        if self.mode == "db":
            rows = db.q(
                "SELECT coalesce(sum(net_pnl),0) FROM positions "
                "WHERE status='closed' AND mode='live' "
                "AND (exit_ts AT TIME ZONE 'UTC')::date = %s::date",
                (now.strftime("%Y-%m-%d"),))
            return float(rows[0][0])
        day = now.strftime("%Y-%m-%d")
        return sum(p["net_pnl"] for p in self.closed
                   if p["exit_ts"].strftime("%Y-%m-%d") == day)

    def can_open(self, strategy: str, symbol: str, group: str, now: dt.datetime) -> bool:
        pos = self.open_positions()
        if len(pos) >= self.cfg["max_open_positions"]:
            return False
        if len([p for p in pos if p["grp"] == group]) >= self.cfg["max_positions_per_group"]:
            return False
        if len([p for p in pos if p["strategy"] == strategy]) >= self.cfg["max_positions_per_strategy"]:
            return False
        if any(p["symbol"] == symbol and p["strategy"] == strategy for p in pos):
            return False
        day_pnl_pct = 100 * self.realized_today(now) / max(self.equity, 1)
        if day_pnl_pct <= self.cfg["daily_stop_pct"]:
            return False
        return True

    # ---------- lifecycle ----------
    def open(self, *, strategy: str, symbol: str, group: str, side: str,
             price: float, sl: float, tp: float, regime: str,
             ts: dt.datetime, reason: str = "") -> dict | None:
        slip = self.cfg["slippage"]
        entry = price * (1 + slip) if side == "long" else price * (1 - slip)
        # reject signals with SL/TP on the wrong side of entry (strategy bug)
        ok = (sl < entry < tp) if side == "long" else (tp < entry < sl)
        if not ok:
            if self.mode == "db":
                db.event("error", f"{strategy} rejected: bad SL/TP geometry "
                                  f"{side} {symbol} e={entry:.6g} sl={sl:.6g} tp={tp:.6g}")
            return None
        sl_dist = abs(entry - sl) / entry
        if sl_dist <= 0.0005:  # SL too tight to size sanely
            return None
        risk_amount = self.equity * self.cfg["risk_per_trade"]
        notional = min(risk_amount / sl_dist, self.equity * self.cfg["max_leverage"])
        qty = notional / entry
        fee = notional * self.cfg["fee_taker"]
        lev = notional / self.equity
        pos = dict(strategy=strategy, symbol=symbol, grp=group, side=side, qty=qty,
                   lev=round(lev, 2), entry_price=entry, entry_ts=ts, sl=sl, tp=tp,
                   status="open", fees=fee, funding_paid=0.0, regime_entry=regime,
                   notional=notional, mode="live" if self.mode == "db" else "bt")
        if self.mode == "db":
            rows = db.q(
                "INSERT INTO positions (strategy,symbol,grp,side,qty,lev,entry_price,"
                "entry_ts,sl,tp,status,fees,funding_paid,regime_entry,notional,mode) "
                "VALUES (%(strategy)s,%(symbol)s,%(grp)s,%(side)s,%(qty)s,%(lev)s,"
                "%(entry_price)s,%(entry_ts)s,%(sl)s,%(tp)s,'open',%(fees)s,0,"
                "%(regime_entry)s,%(notional)s,%(mode)s) RETURNING id", pos)
            pos["id"] = rows[0][0]
            db.event("trade", f"OPEN {side.upper()} {symbol} @ {entry:.6g} [{strategy}] {reason}",
                     {"id": pos["id"], "sl": sl, "tp": tp})
        else:
            pos["id"] = len(self.positions) + 1
            self.positions.append(pos)
        return pos

    def close(self, pos: dict, price: float, ts: dt.datetime, reason: str) -> dict:
        slip = self.cfg["slippage"]
        exit_p = price * (1 - slip) if pos["side"] == "long" else price * (1 + slip)
        sign = 1 if pos["side"] == "long" else -1
        gross = (exit_p - pos["entry_price"]) * pos["qty"] * sign
        fees = pos["fees"] + pos["qty"] * exit_p * self.cfg["fee_taker"]
        net = gross - fees - pos["funding_paid"]
        self.equity += net
        pos.update(status="closed", exit_price=exit_p, exit_ts=ts, exit_reason=reason,
                   gross_pnl=gross, fees=fees, net_pnl=net)
        if self.mode == "db":
            db.execute(
                "UPDATE positions SET status='closed', exit_price=%s, exit_ts=%s, "
                "exit_reason=%s, gross_pnl=%s, fees=%s, net_pnl=%s WHERE id=%s",
                (exit_p, ts, reason, gross, fees, net, pos["id"]))
            db.event("trade",
                     f"CLOSE {pos['symbol']} {'+' if net >= 0 else ''}{net:.2f} USDT "
                     f"[{pos['strategy']}] {reason}", {"id": pos["id"]})
        else:
            self.closed.append(pos)
        return pos

    def apply_funding(self, pos: dict, rate: float) -> None:
        """Longs pay positive funding, shorts receive it (and vice versa)."""
        sign = 1 if pos["side"] == "long" else -1
        cost = pos["notional"] * rate * sign
        pos["funding_paid"] = pos.get("funding_paid", 0.0) + cost
        if self.mode == "db":
            db.execute("UPDATE positions SET funding_paid=%s WHERE id=%s",
                       (pos["funding_paid"], pos["id"]))

    def check_sl_tp(self, pos: dict, high: float, low: float,
                    ts: dt.datetime) -> dict | None:
        """Intrabar SL/TP check; if both could have hit, assume SL (conservative)."""
        if pos["side"] == "long":
            if low <= pos["sl"]:
                return self.close(pos, pos["sl"], ts, "stop-loss")
            if high >= pos["tp"]:
                return self.close(pos, pos["tp"], ts, "take-profit")
        else:
            if high >= pos["sl"]:
                return self.close(pos, pos["sl"], ts, "stop-loss")
            if low <= pos["tp"]:
                return self.close(pos, pos["tp"], ts, "take-profit")
        return None
