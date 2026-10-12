"""Garde fou des images Docker, sans Docker : le 11/10/2026, l'API a planté en boucle au démarrage parce que
backend/sentinel.py n'était pas copié par backend/Dockerfile, alors que les tests passaient depuis le dépôt.

Pour chaque image, on suit les imports depuis ses points d'entrée (analyse du code, sans l'exécuter) et on vérifie :
1. que chaque module local atteint est copié par son Dockerfile ;
2. que chaque bibliothèque tierce atteinte est déclarée dans son requirements.txt (sauf import protégé par
   try … except ImportError, comme scipy et pyproj dans la fusion, absents de l'image des scripts à dessein).
La vérification en vrai (image construite, démarrée) est scripts/verifier_images.sh, sur le Mac avant publication."""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STDLIB = set(sys.stdlib_module_names)
# Paquet pip vers module importé, et dépendances apportées par un paquet déclaré
PIP = {"psycopg[binary]": "psycopg", "pyyaml": "yaml", "pyshp": "shapefile", "uvicorn[standard]": "uvicorn"}
BRINGS = {"pandas": {"numpy", "dateutil"}, "boto3": {"botocore"}, "requests": {"urllib3"}, "fastapi": {"pydantic", "starlette"}}


def _requirements(path: Path) -> set[str]:
    out = set()
    for line in path.read_text().splitlines():
        name = re.split(r"[<>=;\s]", line.strip(), maxsplit=1)[0]
        if name and not name.startswith("#"):
            mod = PIP.get(name.lower(), name.lower().replace("-", "_"))
            out |= {mod, *BRINGS.get(mod, set())}
    return out


def _imports(path: Path) -> list[tuple[str, bool, bool]]:
    """Modules importés par un fichier : (module, protégé par try … except ImportError, chargé au démarrage). Un import
    dans une fonction n'est chargé qu'à l'appel : il ne peut pas faire planter le démarrage."""
    tree = ast.parse(path.read_text())
    guarded, lazy = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
                isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError") for h in node.handlers):
            guarded |= {id(n) for sub in node.body for n in ast.walk(sub)}
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lazy |= {id(n) for sub in node.body for n in ast.walk(sub)}
    out = []
    for node in ast.walk(tree):
        g, eager = id(node) in guarded, id(node) not in lazy
        if isinstance(node, ast.Import):
            out += [(a.name, g, eager) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.module, g, eager))
            if node.module == "mars":                     # from mars import sentinel, travailleurs
                out += [(f"mars.{a.name}", g, eager) for a in node.names]
    return out


def _local(mod: str, dirs: list[Path]) -> Path | None:
    parts = mod.split(".")
    for d in dirs:
        for cand in (d.joinpath(*parts).with_suffix(".py"), d.joinpath(*parts, "__init__.py")):
            if cand.exists():
                return cand
    return None


def _reach(entries: list[Path], dirs: list[Path]) -> tuple[set[Path], set[str]]:
    """Fichiers locaux atteints depuis les points d'entrée (au démarrage ou à l'appel d'une fonction), et bibliothèques
    tierces chargées au démarrage, non protégées."""
    seen, eager_seen, third = set(), set(), set()
    todo = [(f, True) for f in entries]
    while todo:
        f, eager = todo.pop()
        if (f in eager_seen) or (not eager and f in seen):
            continue
        seen.add(f)
        if eager:
            eager_seen.add(f)
        for mod, guarded, at_load in _imports(f):
            top = mod.split(".")[0]
            local = _local(mod, dirs) or (_local(top, dirs) if top == "mars" else None)
            if local:
                todo.append((local, eager and at_load))
                if mod.startswith("mars."):               # paquets intermédiaires (mars/fusion/__init__.py…)
                    parent = _local(".".join(mod.split(".")[:-1]), dirs)
                    if parent:
                        todo.append((parent, eager and at_load))
            elif top not in STDLIB and not guarded and top != "mars" and eager and at_load:
                third.add(top)
    return seen, third


def _copied(dockerfile: Path) -> list[str]:
    out = []
    for line in dockerfile.read_text().splitlines():
        if line.startswith("COPY "):
            out += line.split()[1:-1]
    return out


def test_backend_image_copies_every_module_app_imports():
    seen, third = _reach([ROOT / "backend" / "app.py"], [ROOT / "backend", ROOT])
    copied = _copied(ROOT / "backend" / "Dockerfile")
    for f in sorted(seen):
        rel = f.relative_to(ROOT).as_posix()
        if rel.startswith("backend/"):
            assert rel in copied, f"{rel} importé par l'API mais absent de backend/Dockerfile (COPY)"
        else:
            assert rel.split("/")[0] in copied or any(rel.startswith(c.rstrip("/") + "/") for c in copied), rel
    missing = third - _requirements(ROOT / "backend" / "requirements.txt")
    assert not missing, f"bibliothèques importées par l'API, absentes de backend/requirements.txt : {sorted(missing)}"


def test_scripts_image_has_what_its_entry_points_import():
    entries = [ROOT / "scripts" / n for n in ("taches.py", "ais_live.py", "ais_ingest.py", "import_watchlist.py",
                                              "build_masks.py", "mesures_calibration.py")]
    seen, third = _reach(entries, [ROOT / "scripts", ROOT])
    copied = _copied(ROOT / "scripts" / "Dockerfile")
    tops = {f.relative_to(ROOT).parts[0] for f in seen}
    assert tops <= {c.rstrip("/").split("/")[0] for c in copied}, (tops, copied)
    missing = third - _requirements(ROOT / "scripts" / "requirements.txt")
    assert not missing, f"bibliothèques importées par les scripts, absentes de scripts/requirements.txt : {sorted(missing)}"


def test_the_guard_would_have_caught_the_missing_sentinel_module(tmp_path):
    """Le Dockerfile du 11/10 (sans backend/sentinel.py) est refusé."""
    old = (ROOT / "backend" / "Dockerfile").read_text().replace(" backend/sentinel.py", "")
    (tmp_path / "Dockerfile").write_text(old)
    seen, _ = _reach([ROOT / "backend" / "app.py"], [ROOT / "backend", ROOT])
    rels = {f.relative_to(ROOT).as_posix() for f in seen}
    assert "backend/sentinel.py" in rels and "backend/sentinel.py" not in _copied(tmp_path / "Dockerfile")
