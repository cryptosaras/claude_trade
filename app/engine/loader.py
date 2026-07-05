"""Hot-loads strategy files from strategies/. A file change (mtime) triggers
reload on the next engine tick — no restart needed when the AI edits strategies."""
import importlib.util
import logging
import sys

from psycopg.types.json import Json

from ..common import db
from ..common.config import STRATEGIES_DIR

log = logging.getLogger("loader")
_cache: dict[str, tuple[float, object]] = {}

TRADING_STATUSES = {"active", "incubating", "locked"}


def _load_file(path) -> object | None:
    spec = importlib.util.spec_from_file_location(f"strategies.{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    strat = getattr(mod, "STRATEGY", None)
    if strat is None or not getattr(strat, "meta", None):
        log.warning("%s has no STRATEGY object, skipped", path.name)
        return None
    return strat


def load_strategies(sync_db: bool = True) -> list:
    """Returns strategy objects. DB row status wins over file meta status
    (the API can pause/resume without touching files)."""
    strategies = []
    for path in sorted(STRATEGIES_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        mtime = path.stat().st_mtime
        cached = _cache.get(path.name)
        if cached and cached[0] == mtime:
            strat = cached[1]
        else:
            try:
                strat = _load_file(path)
            except Exception as e:  # noqa: BLE001
                log.exception("failed to load %s", path.name)
                if sync_db:
                    db.event("error", f"strategy load failed: {path.name}: {e}")
                continue
            if strat is None:
                continue
            _cache[path.name] = (mtime, strat)
            if cached:
                log.info("reloaded %s v%s", strat.meta["name"], strat.meta.get("version"))
                if sync_db:
                    db.event("system", f"strategy reloaded: {strat.meta['name']} "
                                       f"v{strat.meta.get('version')}")
        strategies.append(strat)

    if sync_db:
        for s in strategies:
            m = s.meta
            db.execute(
                "INSERT INTO strategies (name, status, meta) VALUES (%s, %s, %s) "
                "ON CONFLICT (name) DO UPDATE SET meta=EXCLUDED.meta",
                (m["name"], m.get("status", "active"), Json(m)))
        rows = dict(db.q("SELECT name, status FROM strategies"))
        for s in strategies:
            s.runtime_status = rows.get(s.meta["name"], s.meta.get("status", "active"))
    else:
        for s in strategies:
            s.runtime_status = s.meta.get("status", "active")
    return strategies
