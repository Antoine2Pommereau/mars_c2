"""Configuration : variables d'environnement (.env) et règles versionnées (config/rules.yaml)."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path = ROOT / ".env") -> None:
    """Charge un fichier .env simple (CLE=valeur) sans écraser l'environnement existant."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_rules(path: Path = ROOT / "config" / "rules.yaml") -> dict:
    return yaml.safe_load(path.read_text())


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://mars:mars@localhost:5432/mars")
