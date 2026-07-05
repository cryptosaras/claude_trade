"""Strategy interface. Every strategy is one file in strategies/ defining a
class that subclasses Strategy and a module-level STRATEGY = MyStrategy().

meta fields:
  name        unique id, matches filename ideally
  version     int, bump on every edit
  description one line, plain language
  groups      which coin groups it trades: subset of universe.yaml groups
  regimes     when it may trade: subset of ["BULL", "BEAR", "SIDE"]
  status      active | incubating | locked | paused | retired
              - active/incubating/locked all trade live (incubating = on probation,
                gate not yet passed; locked = met the daily-PnL goal, DO NOT EDIT)
              - paused/retired do not trade
  params      dict of tunables — keep every magic number here so tuning is visible

Contract:
  should_enter(ctx) -> Signal | None  — called for each symbol of its groups
                                        when the regime matches
  should_exit(ctx, pos) -> str | None — return exit reason, or None to keep.
                                        SL/TP/max-hold are enforced by the engine.
"""
from dataclasses import dataclass


@dataclass
class Signal:
    side: str          # "long" | "short"
    sl: float          # absolute stop-loss price
    tp: float          # absolute take-profit price
    reason: str = ""


class Strategy:
    meta: dict = {}

    def should_enter(self, ctx):
        raise NotImplementedError

    def should_exit(self, ctx, pos):
        return None
