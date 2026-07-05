import os
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "config"
STRATEGIES_DIR = ROOT / "strategies"
REPORTS_DIR = ROOT / "reports"


def load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_universe() -> dict:
    """Returns {group_name: [symbols]} plus helper maps."""
    with open(CONFIG_DIR / "universe.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    groups = {g: spec["symbols"] for g, spec in raw["groups"].items()}
    sym_group = {s: g for g, syms in groups.items() for s in syms}
    collect = raw.get("collect", {}) or {}
    return {"groups": groups, "symbol_group": sym_group, "symbols": list(sym_group),
            "collect": {"min_turnover_usd": float(collect.get("min_turnover_usd", 5e6)),
                        "max_symbols": int(collect.get("max_symbols", 900))}}


def db_dsn() -> str:
    return os.environ.get(
        "DATABASE_URL",
        f"postgresql://trade:{os.environ.get('POSTGRES_PASSWORD', 'trade')}"
        f"@{os.environ.get('DB_HOST', 'db')}:5432/trade",
    )
