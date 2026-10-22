"""Calendrier des échéances des secrets (lot Z1) : dates d'expiration déclarées dans le .env (variables *_EXPIRENT_LE,
format 14/11/2026 ou 2026-11-14), relevées chaque jour par taches (tâche « echeances »), écrites dans la table
echeances (la date seule, jamais le secret), notifiées par ntfy 14 jours puis 3 jours avant, puis à l'expiration, une
fois par palier et par date. Une échéance sans date déclarée est signalée comme telle (« non déclarée »). Affichées dans
Coulisses, rubrique Système (backend/controle.py, /api/controle/systeme). Paliers : config/rules.yaml, echeances."""
import os
import re
from datetime import date, datetime, timezone

from mars import traces

# Clé, libellé, variable du .env qui porte la date
ECHEANCES = [
    ("cdse_serveur", "Clés Copernicus du serveur", "CDSE_CLES_EXPIRENT_LE"),
    ("cdse_travailleur", "Clés Copernicus des travailleurs", "CDSE_TRAVAILLEUR_EXPIRENT_LE"),
    ("earthdata", "Jeton NASA Earthdata (VIIRS)", "EARTHDATA_EXPIRENT_LE"),
    ("github_lecture", "Jeton GitHub de lecture (serveur)", "GITHUB_LECTURE_EXPIRENT_LE"),
    ("github_ecriture", "Jeton GitHub d'écriture (Mac)", "GITHUB_ECRITURE_EXPIRENT_LE"),
    ("scaleway", "Clé de l'API Scaleway", "SCW_CLE_EXPIRENT_LE"),
]


def lire_date(v: str | None) -> tuple[date | None, bool]:
    """(date, illisible) : JJ/MM/AAAA ou AAAA-MM-JJ ; (None, False) si absente."""
    v = (v or "").strip()
    if not v:
        return None, False
    for motif, ordre in ((r"(\d{2})/(\d{2})/(\d{4})", (3, 2, 1)), (r"(\d{4})-(\d{2})-(\d{2})", (1, 2, 3))):
        m = re.fullmatch(motif, v)
        if m:
            try:
                return date(*(int(m.group(i)) for i in ordre)), False
            except ValueError:
                return None, True
    return None, True


def palier(jours: int | None, paliers: list[int]) -> str | None:
    """Palier atteint : « expiree » (date passée), « j3 », « j14 »… (le plus petit palier qui contient l'écart), ou
    None (loin, ou pas de date)."""
    if jours is None:
        return None
    if jours < 0:
        return "expiree"
    for p in sorted(paliers):
        if jours <= p:
            return f"j{p}"
    return None


def etat(env: dict | None = None, aujourd_hui: date | None = None, paliers=(14, 3)) -> list[dict]:
    """Échéances lues dans l'environnement : clé, libellé, variable, date, jours restants, palier, état lisible."""
    env = os.environ if env is None else env
    jour = aujourd_hui or datetime.now(timezone.utc).date()
    out = []
    for cle, libelle, var in ECHEANCES:
        d, illisible = lire_date(env.get(var))
        jours = (d - jour).days if d else None
        out.append({"cle": cle, "libelle": libelle, "variable": var, "expire_le": d, "illisible": illisible,
                    "jours": jours, "palier": palier(jours, list(paliers))})
    return out


def message(e: dict) -> str:
    d = e["expire_le"].strftime("%d/%m/%Y")
    if e["palier"] == "expiree":
        return f"{e['libelle']} expirée depuis le {d} : la renouveler, puis mettre à jour {e['variable']} dans le .env"
    return f"{e['libelle']} expire le {d}, dans {e['jours']} jour(s) : la renouveler, puis mettre à jour {e['variable']}"


def relever(conn, rules: dict, aujourd_hui: date | None = None, notifier=None) -> dict:
    """Tâche quotidienne (psycopg) : écrit l'état de chaque échéance, notifie un palier nouveau pour une date donnée
    (14 jours, 3 jours, expirée : une seule fois chacun ; une date changée repart de zéro). `notifier(categorie,
    gravite, titre, message)` : controle.notifier par défaut (préférences et heures calmes du panneau)."""
    from mars import controle
    paliers = (rules.get("echeances") or {}).get("paliers_jours", [14, 3])
    notifier = notifier or (lambda *a: controle.notifier(*a, conn=conn, rules=rules))
    out = {"declarees": 0, "non_declarees": [], "illisibles": [], "proches": [], "notifiees": 0}
    for e in etat(aujourd_hui=aujourd_hui, paliers=paliers):
        avant = conn.execute("SELECT expire_le, notifiee FROM echeances WHERE cle = %s", (e["cle"],)).fetchone()
        deja = avant[1] if avant and avant[0] == e["expire_le"] else None
        notifiee = deja
        if e["palier"] and e["palier"] != deja:
            grave = e["palier"] in ("expiree", f"j{min(paliers)}")
            with traces.span("echeances.notification", **{"echeance.cle": e["cle"], "echeance.palier": e["palier"]}):
                notifier("pannes", "elevee" if grave else "moyenne", "MARS C2 : échéance d'un secret", message(e))
            notifiee = e["palier"]
            out["notifiees"] += 1
        conn.execute(
            """INSERT INTO echeances (cle, libelle, variable, expire_le, illisible, notifiee, mis_a_jour_le)
               VALUES (%s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (cle) DO UPDATE SET libelle = EXCLUDED.libelle, variable = EXCLUDED.variable,
                 expire_le = EXCLUDED.expire_le, illisible = EXCLUDED.illisible, notifiee = EXCLUDED.notifiee,
                 mis_a_jour_le = now()""",
            (e["cle"], e["libelle"], e["variable"], e["expire_le"], e["illisible"], notifiee))
        if e["expire_le"]:
            out["declarees"] += 1
        elif e["illisible"]:
            out["illisibles"].append(e["variable"])
        else:
            out["non_declarees"].append(e["variable"])
        if e["palier"]:
            out["proches"].append(f"{e['cle']} : {e['palier']}")
    traces.attributs(**{"echeances.declarees": out["declarees"], "echeances.notifiees": out["notifiees"],
                        "echeances.non_declarees": len(out["non_declarees"])})
    return out
