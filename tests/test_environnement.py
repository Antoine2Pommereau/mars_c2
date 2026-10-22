"""Lot Z1 : un environnement par service. Aucun conteneur ne reçoit le .env en entier ; docker-compose.yml et sa
surcouche du serveur donnent à chaque service une liste explicite de variables. Ce test confronte, pour chaque service,
ces listes aux variables que lit le code qu'il exécute (son point d'entrée et tous les modules du dépôt qu'il importe,
même tardivement) :
* variable donnée mais jamais lue : échec (un secret de trop dans un conteneur) ;
* variable lue mais absente : échec, sauf si elle figure dans FACULTATIVES avec sa raison (valeur par défaut voulue,
  essai manuel, module importé dont cette partie ne sert pas dans ce service) ;
* les secrets les plus sensibles ne vont qu'au service qui s'en sert.
Une variable « lue » est une chaîne littérale du code égale au nom d'une variable connue (os.environ, getenv, tables
de noms comme r2_signature.MODELES)."""
import ast
import io
import re
import tokenize
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

# Point d'entrée de chaque service (commande, contrôle de santé, scripts lancés à la main dans le conteneur)
ENTREES = {
    "backend": ["backend/app.py"],
    "collector": ["scripts/ais_live.py", "scripts/sante.py"],
    "ingest": ["scripts/ais_ingest.py", "scripts/sante.py"],
    "taches": ["scripts/taches.py", "scripts/build_masks.py", "scripts/mesures_calibration.py"],
}

# Lue par le code du service, volontairement absente de son environnement : valeur par défaut du code, ou partie d'un
# module importé qui ne sert pas dans ce service. Toute nouvelle lecture doit être donnée ou ajoutée ici, avec sa raison.
DEFAUT = "valeur par défaut du code"
FACULTATIVES = {
    "*": {
        "MARS_VERSION": "posée par le Dockerfile (empreinte du commit)",
        "MARS_ENVIRONNEMENT": DEFAUT, "OTEL_SDK_DISABLED": "coupure des traces, tests seulement",
        "GRAFANA_URL": DEFAUT, "OTEL_METRIQUES_URL": "relevé du collecteur, propre à taches",
        "TRACEPARENT": "travailleurs seulement (otel_mini)", "AIS_LIVE_DIR": DEFAUT,
    },
    "backend": {
        "SCW_SECRET_KEY": "mars.travailleurs importé pour les états ; la clé Scaleway ne va qu'à taches",
        "SCW_PROJECT_ID": "idem", "SCW_PRIVATE_NETWORK_ID": "idem", "SCW_SERVEUR_PRINCIPAL": "idem",
        "MARS_IP_PRIVEE": "adresse de retour des travailleurs, écrite par taches au lancement",
        "CDSE_ACCESS_KEY": "paire Copernicus du serveur : taches seulement", "CDSE_SECRET_KEY": "idem",
        "CDSE_CLES_EXPIRENT_LE": "échéances relevées par taches", "CDSE_TRAVAILLEUR_EXPIRENT_LE": "idem",
        "SENTINEL_CALCUL_ESSAI": "essai manuel, taches seulement", "SENTINEL_PLAFOND_ESSAI": "idem",
        "GRAFANA_PG_MOT_DE_PASSE": "rôle de lecture de Grafana, posé par taches",
        "SEUIL_COLLECTE_MIN": "seuils de la supervision (taches)", "SEUIL_INGESTION_MIN": "idem",
        "SEUIL_DISQUE_LIBRE_PCT": "idem", "SEUIL_DISQUE_ORANGE_PTS": "idem", "SEUIL_COLLECTEUR_MUET_MIN": "idem",
        "HEURE_NUIT_VIIRS": "idem", "ECHECS_SUCCESSIFS": "idem",
    },
    "collector": {
        "AIS_FLUSH_S": DEFAUT, "AIS_MAX_EN_ATTENTE": DEFAUT,
        "NTFY_SUJET": "mars.supervision importé ; les notifications partent de taches", "NTFY_URL": "idem",
        "GRAFANA_PG_MOT_DE_PASSE": "rôle de Grafana, posé par taches",
    },
    "ingest": {
        "AIS_INGEST_INTERVAL_S": DEFAUT,
        "NTFY_SUJET": "mars.supervision importé ; les notifications partent de taches", "NTFY_URL": "idem",
        "GRAFANA_PG_MOT_DE_PASSE": "rôle de Grafana, posé par taches",
    },
    "taches": {
        "MARS_OPERATEURS": "empreintes des opérateurs : l'API seulement (controle-diagnostic lit la session par l'API)",
        "MARS_OPERATEUR": "nom de l'auteur d'une commande manuelle (taches.py satellites-pause), facultatif",
        "SENTINEL_CALCUL_ESSAI": "essai manuel (docker compose run -e)", "SENTINEL_PLAFOND_ESSAI": "idem",
        "DEPLOIEMENT": "posée par scripts/deployer.sh (docker compose exec -e)",
        "ESSAI_CIBLE": "posée par scripts/essai_restauration.sh (docker compose exec -e)",
    },
}

# Secret : services qui peuvent le recevoir, et eux seuls
PLACEMENT = {
    "SCW_SECRET_KEY": {"taches"}, "CDSE_ACCESS_KEY": {"taches"}, "CDSE_SECRET_KEY": {"taches"},
    "MARS_OPERATEURS": {"backend"}, "AISSTREAM_API_KEY": {"collector"}, "EARTHDATA_TOKEN": {"backend"},
    "GRAFANA_ADMIN_MOT_DE_PASSE": set(), "GF_SECURITY_ADMIN_PASSWORD": {"grafana"},
    "CDSE_TRAVAILLEUR_SECRET_KEY": {"backend", "taches"},
}


class _Chargeur(yaml.SafeLoader):
    pass


_Chargeur.add_constructor("!reset", lambda l, n: None)
_Chargeur.add_constructor("!override", lambda l, n: l.construct_sequence(n) if isinstance(n, yaml.SequenceNode)
                          else l.construct_mapping(n))


def _compose(nom):
    return yaml.load((ROOT / nom).read_text(), Loader=_Chargeur)


def _env_file(fichiers) -> set[str]:
    out = set()
    for f in [fichiers] if isinstance(fichiers, str) else fichiers or []:
        assert f != ".env" and not f.endswith("/.env"), "le .env entier ne doit aller à aucun service"
        out |= {ligne.split("=", 1)[0].strip() for ligne in (ROOT / f).read_text().splitlines()
                if "=" in ligne and not ligne.lstrip().startswith("#")}
    return out


def environnements() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for nom in ("docker-compose.yml", "docker-compose.serveur.yml"):
        for svc, d in (_compose(nom).get("services") or {}).items():
            env = d.get("environment") or {}
            if isinstance(env, list):
                env = dict(e.split("=", 1) if "=" in e else (e, None) for e in env)
            out.setdefault(svc, set()).update(env)
            out[svc] |= _env_file(d.get("env_file"))
    return out


def _module(nom: str, dossier: Path) -> Path | None:
    parts = nom.split(".")
    for base in (ROOT, ROOT / "backend", ROOT / "scripts", dossier):
        p = base.joinpath(*parts)
        if p.with_suffix(".py").exists():
            return p.with_suffix(".py")
        if (p / "__init__.py").exists():
            return p / "__init__.py"
    return None


def graphe(entrees: list[str]) -> set[Path]:
    vus: set[Path] = set()
    pile = [ROOT / e for e in entrees]
    while pile:
        f = pile.pop()
        if f in vus:
            continue
        vus.add(f)
        for n in ast.walk(ast.parse(f.read_text())):
            noms = []
            if isinstance(n, ast.Import):
                noms = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                noms = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
            for m in noms:
                g = _module(m, f.parent)
                if g is not None and g not in vus:
                    pile.append(g)
    return vus


LECTURE = re.compile(r"""(?:environ\.get|getenv)\(\s*["']([A-Z0-9_]+)|environ\[\s*["']([A-Z0-9_]+)""")


def univers(envs: dict[str, set[str]]) -> set[str]:
    noms = set().union(*envs.values())
    for f in list(ROOT.glob("mars/**/*.py")) + list(ROOT.glob("backend/*.py")) + list(ROOT.glob("scripts/*.py")):
        noms |= {next(x for x in m.groups() if x) for m in LECTURE.finditer(f.read_text())}
    noms |= _env_file(".env.example")
    return noms


def lues(fichiers: set[Path], noms: set[str]) -> set[str]:
    out = set()
    for f in fichiers:
        for tok in tokenize.generate_tokens(io.StringIO(f.read_text()).readline):
            if tok.type == tokenize.STRING:
                try:
                    v = ast.literal_eval(tok.string)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(v, str) and v in noms:
                    out.add(v)
    return out


@pytest.fixture(scope="module")
def etat():
    envs = environnements()
    noms = univers(envs)
    return envs, {svc: lues(graphe(e), noms) for svc, e in ENTREES.items()}


@pytest.mark.parametrize("svc", sorted(ENTREES))
def test_each_service_gets_exactly_what_its_code_reads(etat, svc):
    envs, lu = etat
    donne, lit = envs[svc], lu[svc]
    fac = {**FACULTATIVES["*"], **FACULTATIVES.get(svc, {})}
    assert not sorted(donne - lit), f"{svc} : variables données mais jamais lues par son code"
    assert not sorted(lit - donne - set(fac)), f"{svc} : variables lues mais absentes (à donner, ou FACULTATIVES)"
    perimees = sorted(set(FACULTATIVES.get(svc, {})) - lit)
    assert not perimees, f"{svc} : FACULTATIVES qui ne sont plus lues : {perimees}"
    assert not (set(FACULTATIVES.get(svc, {})) & donne), f"{svc} : facultative pourtant donnée"


def test_sensitive_secrets_go_only_where_they_are_used(etat):
    envs, _ = etat
    for var, permis in PLACEMENT.items():
        ont = {svc for svc, e in envs.items() if var in e}
        assert ont <= permis, f"{var} donnée à {sorted(ont - permis)}"


def test_no_service_receives_the_whole_env_file():
    for nom in ("docker-compose.yml", "docker-compose.serveur.yml"):
        assert not re.search(r"env_file:.*(?<![\w/])\.env\b(?!\.)", (ROOT / nom).read_text()), nom


def test_overlay_anchors_match_base():
    """YAML ne partage pas les ancres entre fichiers : la surcouche les recopie, à l'identique."""
    def ancres(nom):
        return {k: v for k, v in _compose(nom).items() if k.startswith("x-") and k != "x-journaux" and k != "x-traces"}
    base, surcouche = ancres("docker-compose.yml"), ancres("docker-compose.serveur.yml")
    assert base and base == surcouche
