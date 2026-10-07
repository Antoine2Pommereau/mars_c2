"""Orchestrateur des travailleurs éphémères (étape 3, lots B et C), dans le conteneur taches.

Le serveur décide et reçoit ; il ne télécharge jamais d'images satellites. Un travailleur est une instance Scaleway
créée à la demande (API Scaleway, clé limitée au projet), dans cet ordre, chaque étape vérifiée (provision) :
création éteinte, rattachement au réseau privé (attendu jusqu'à « available »), cloud-init (avec l'adresse MAC de la
carte privée), démarrage (attendu jusqu'à « running »). Au démarrage, travailleurs/demarrage.sh configure l'interface
privée en DHCP, contacte le serveur, envoie son journal, tire l'image d'analyse depuis le registre GitHub, lance le
script (travailleurs/*.py) qui télécharge les données, analyse et renvoie le résultat (jeton propre à l'exécution) ;
puis le serveur détruit l'instance. Une étape en échec : lancement en échec, instance détruite aussitôt.

Garde fous : délai de démarrage (aucun signe de vie au bout de 10 minutes : destruction), durée de vie maximale
(destruction forcée), un seul travailleur actif par verrou (index unique en base : ni le rattrapage automatique ni la
commande manuelle ne peuvent lancer deux fois les mêmes nuits), plafonds quotidiens (nombre et minutes), journal de chaque
exécution dans task_runs (durée, coût estimé, résultat), et à chaque minute une réconciliation avec Scaleway : toute
instance étiquetée « mars-c2-travailleur » qui ne correspond pas à un travailleur actif est détruite (aucun orphelin,
même après un redémarrage du serveur ou une base restaurée).

Garde de destruction : aucune instance n'est détruite sans passer par Scaleway.destroy, qui relit l'instance chez
Scaleway et refuse (RefusDestruction) tout ce qui n'est pas un travailleur : serveur principal (identifiant de
SCW_SERVEUR_PRINCIPAL, ou lu dans les métadonnées de l'instance qui exécute ce code), instance protégée contre la
suppression, autre projet, nom autre que « mars-travailleur-<n> », étiquettes « mars-c2-travailleur » et « run-<n> »
absentes ou discordantes. Le filtre d'étiquette de l'API n'est jamais pris pour acquis.

Les fonctions de décision (plafonds, coût, cloud-init, orphelins, garde) sont pures et couvertes par
tests/test_travailleurs.py.
"""
import base64
import hashlib
import json
import math
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from mars.config import ROOT

API = "https://api.scaleway.com"
TAG = "mars-c2-travailleur"
NAME = re.compile(r"^mars-travailleur-(\d+)$")
METADATA = "http://169.254.42.42/conf?format=json"     # métadonnées de l'instance courante (Scaleway, sans jeton)
GONE = object()


class ScalewayError(RuntimeError):
    pass


class RefusDestruction(ScalewayError):
    """Destruction refusée : l'instance n'est pas un travailleur."""


def refus(server: dict, project: str | None, protected: set[str]) -> str | None:
    """Motif de refus de détruire `server` (réponse de l'API Scaleway), ou None si c'est bien un travailleur. Toutes
    les conditions sont requises ; une seule suffit à refuser."""
    sid, name, tags = server.get("id"), server.get("name") or "", server.get("tags") or []
    if not sid or sid in protected:
        return f"instance {sid} protégée : serveur principal"
    if server.get("protected"):
        return f"instance {sid} ({name}) protégée contre la suppression chez Scaleway"
    if project and server.get("project") not in (None, project):
        return f"instance {sid} ({name}) d'un autre projet"
    m = NAME.match(name)
    if not m:
        return f"instance {sid} : le nom « {name} » n'est pas celui d'un travailleur"
    if TAG not in tags or f"run-{m.group(1)}" not in tags:
        return f"instance {sid} ({name}) : étiquettes {tags} sans « {TAG} » et « run-{m.group(1)} »"
    return None


def protected_ids(session=None) -> set[str]:
    """Identifiants à ne jamais détruire : SCW_SERVEUR_PRINCIPAL (liste séparée par des virgules), plus l'instance qui
    exécute ce code si ses métadonnées sont lisibles (le serveur principal lui même)."""
    ids = {x.strip() for x in os.environ.get("SCW_SERVEUR_PRINCIPAL", "").split(",") if x.strip()}
    try:
        import requests
        r = (session or requests).get(METADATA, timeout=2)
        if r.ok and r.json().get("id"):
            ids.add(r.json()["id"])
    except Exception:
        pass                                            # hors Scaleway, ou métadonnées injoignables depuis le conteneur
    return ids


class Scaleway:
    """Client minimal de l'API Instance (et du catalogue d'images) de Scaleway."""

    def __init__(self, secret_key: str, project_id: str, session=None, protected: set[str] | None = None):
        import requests
        self.project = project_id
        self.s = session or requests.Session()
        self.s.headers.update({"X-Auth-Token": secret_key, "Content-Type": "application/json"})
        self.protected = protected if protected is not None else protected_ids()

    @classmethod
    def from_env(cls):
        key, project = os.environ.get("SCW_SECRET_KEY"), os.environ.get("SCW_PROJECT_ID")
        return cls(key, project) if key and project else None

    def _req(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{API}{path}", timeout=60, **kw)
        if r.status_code == 404:
            return GONE
        if r.status_code >= 400:
            raise ScalewayError(f"{method} {path} : {r.status_code} {r.text[:300]}")
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None

    def image_id(self, zone: str, label: str, commercial_type: str) -> str:
        d = self._req("GET", f"/marketplace/v2/local-images?image_label={label}&zone={zone}&type=instance_local")
        for i in d.get("local_images", []):
            if commercial_type in i.get("compatible_commercial_types", []):
                return i["id"]
        raise ScalewayError(f"aucune image « {label} » pour {commercial_type} en {zone}")

    def create(self, zone: str, name: str, commercial_type: str, image: str, disque_go: int, tags: list) -> dict:
        body = {"name": name, "project": self.project, "commercial_type": commercial_type, "image": image,
                "dynamic_ip_required": True, "tags": tags,
                "volumes": {"0": {"name": name, "size": int(disque_go * 1e9), "volume_type": "l_ssd"}}}
        return self._req("POST", f"/instance/v1/zones/{zone}/servers", json=body)["server"]

    def set_cloud_init(self, zone: str, server_id: str, text: str):
        self._req("PATCH", f"/instance/v1/zones/{zone}/servers/{server_id}/user_data/cloud-init",
                  data=text.encode(), headers={"Content-Type": "text/plain"})

    def attach_private_network(self, zone: str, server_id: str, private_network_id: str, wait_s: float = 120,
                               sleep=time.sleep) -> dict:
        """Rattache l'instance au réseau privé et attend que la carte soit prête (« available ») : le rattachement est
        asynchrone chez Scaleway. Retourne la carte (adresse MAC comprise) ; ScalewayError sinon."""
        nic = self._req("POST", f"/instance/v1/zones/{zone}/servers/{server_id}/private_nics",
                        json={"private_network_id": private_network_id})["private_nic"]
        waited = 0.0
        while nic.get("state") != "available":
            if nic.get("state") in ("syncing_error", "error"):
                raise ScalewayError(f"rattachement au réseau privé en erreur (état {nic.get('state')})")
            if waited >= wait_s:
                raise ScalewayError(f"rattachement au réseau privé non prêt après {wait_s:.0f} s (état {nic.get('state')})")
            sleep(3)
            waited += 3
            d = self._req("GET", f"/instance/v1/zones/{zone}/servers/{server_id}/private_nics/{nic['id']}")
            nic = d["private_nic"] if d is not GONE else {"state": "error"}
        if nic.get("private_network_id") not in (None, private_network_id):
            raise ScalewayError("carte rattachée à un autre réseau privé")
        return nic

    def start(self, zone: str, server_id: str, wait_s: float = 300, sleep=time.sleep):
        """Démarre l'instance et attend l'état « running » ; ScalewayError si elle s'arrête ou tarde."""
        self.action(zone, server_id, "poweron")
        waited = 0.0
        while True:
            s = self.server(zone, server_id)
            state = (s or {}).get("state")
            if state == "running":
                return
            if s is None or (state in ("stopped", "stopped in place") and waited >= 15):
                raise ScalewayError(f"démarrage en échec (état {state})")
            if waited >= wait_s:
                raise ScalewayError(f"démarrage non abouti après {wait_s:.0f} s (état {state})")
            sleep(3)
            waited += 3

    def action(self, zone: str, server_id: str, action: str):
        """Seul le démarrage passe par ici ; toute action destructrice passe par destroy (et sa garde)."""
        if action != "poweron":
            raise RefusDestruction(f"action « {action} » réservée à destroy")
        return self._req("POST", f"/instance/v1/zones/{zone}/servers/{server_id}/action", json={"action": action})

    def server(self, zone: str, server_id: str):
        d = self._req("GET", f"/instance/v1/zones/{zone}/servers/{server_id}")
        return None if d is GONE else d["server"]

    def tagged(self, zone: str) -> list[dict]:
        """Travailleurs de la zone : filtre d'étiquette demandé à l'API, puis revérifié ici (garde complète)."""
        d = self._req("GET", f"/instance/v1/zones/{zone}/servers?tags={TAG}&per_page=100&project={self.project}")
        servers = [] if d is GONE else d.get("servers", [])
        return [s for s in servers if refus(s, self.project, self.protected) is None]

    def destroy(self, zone: str, server_id: str) -> bool:
        """Détruit l'instance et ses volumes, après avoir vérifié chez Scaleway que c'est un travailleur (sinon
        RefusDestruction, sans aucun appel destructeur). Vrai quand plus rien n'existe ; faux si la destruction est en
        cours (un nouvel appel, à la minute suivante, la termine)."""
        if not server_id or server_id in self.protected:
            raise RefusDestruction(f"instance {server_id} protégée : serveur principal")
        s = self.server(zone, server_id)
        if s is None:
            return True
        motif = refus(s, self.project, self.protected)
        if motif:
            raise RefusDestruction(motif)
        state = s.get("state")
        if state == "running":
            # serveur, volumes locaux et adresse dynamique
            self._req("POST", f"/instance/v1/zones/{zone}/servers/{server_id}/action", json={"action": "terminate"})
            return False
        if state in ("stopped", "stopped in place"):
            vols = [v["id"] for v in (s.get("volumes") or {}).values() if v]
            self._req("DELETE", f"/instance/v1/zones/{zone}/servers/{server_id}")
            for v in vols:
                self._req("DELETE", f"/instance/v1/zones/{zone}/volumes/{v}")
            return True
        return False                                            # starting, stopping, locked : à la minute suivante


# Décisions pures

def type_params(rules: dict, tache: str) -> dict:
    p = rules["travailleurs"]
    t = dict(p["types"][tache])
    t.setdefault("facturation_min_min", p["facturation_min_min"])
    return t


def quota(rules: dict, today: list[dict], now: datetime) -> str | None:
    """Motif de refus si un plafond quotidien est atteint (travailleurs créés aujourd'hui : cree_le, fini_le)."""
    p = rules["travailleurs"]
    if len(today) >= p["max_par_jour"]:
        return f"plafond de {p['max_par_jour']} travailleurs par jour atteint"
    minutes = sum(((w["fini_le"] or now) - w["cree_le"]).total_seconds() / 60 for w in today)
    if minutes >= p["max_minutes_par_jour"]:
        return f"plafond de {p['max_minutes_par_jour']} minutes par jour atteint ({minutes:.0f} min)"
    return None


def cost(minutes: float, prix_heure: float, granularite_min: int) -> float:
    """Coût estimé hors taxes : durée arrondie à la granularité de facturation (une heure entamée pour les instances
    CPU, une minute pour les GPU), au moins une unité."""
    units = max(1, math.ceil(max(minutes, 0.01) / granularite_min))
    return round(units * granularite_min / 60 * prix_heure, 4)


def orphans(servers: list[dict], active: dict[str, datetime], now: datetime, max_min: int,
            project: str | None = None, protected: set[str] | frozenset = frozenset()) -> list[str]:
    """Travailleurs à détruire : inconnus de la base (orphelins), ou plus vieux que la durée de vie. Toute instance
    qui n'est pas un travailleur (garde complète, voir refus) est ignorée, quelle que soit la réponse de l'API."""
    out = []
    for s in servers:
        if refus(s, project, set(protected)) is not None:
            continue
        created = s.get("creation_date")
        age = (now - datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds() / 60 if created else 0
        if s["id"] not in active or age > max_min + 5:
            out.append(s["id"])
    return out


def cloud_init(script: str, tache: dict, env: dict, image: str, mac: str, gpu: bool = False) -> str:
    """Configuration cloud-init : script de démarrage (travailleurs/demarrage.sh), script et tâche du travailleur,
    secrets en 0600 ; `mac` : adresse de la carte du réseau privé, que le démarrage configure explicitement."""
    def f(path, content, mode):
        b = base64.b64encode(content.encode()).decode()
        return f"  - path: {path}\n    encoding: b64\n    permissions: '{mode}'\n    content: {b}\n"
    envtxt = "".join(f"{k}={v}\n" for k, v in env.items())
    name = Path(tache.get("script", "travailleur.py")).name
    boot = {"RETOUR": tache["retour"], "JETON": tache["jeton"], "MAC": mac.lower(), "IMAGE": image, "SCRIPT": name,
            "GPU": "--gpus all" if gpu else ""}
    boottxt = "".join(f"{k}={json.dumps(v)}\n" for k, v in boot.items())
    return ("#cloud-config\nwrite_files:\n" + f("/mars/demarrage.sh", (ROOT / "travailleurs" / "demarrage.sh").read_text(), "0755")
            + f("/mars/demarrage.env", boottxt, "0600") + f(f"/mars/{name}", script, "0644")
            + f("/mars/tache.json", json.dumps(tache), "0600") + f("/mars/env", envtxt, "0600")
            + "runcmd:\n  - [bash, /mars/demarrage.sh]\n")


def provision(scw: "Scaleway", zone: str, name: str, t: dict, tags: list, private_network_id: str,
              user_data, sleep=time.sleep, on_created=None) -> dict:
    """Crée un travailleur dans l'ordre sûr, chaque étape vérifiée : instance éteinte, réseau privé rattaché et prêt,
    cloud-init (qui reçoit l'adresse MAC de la carte privée), démarrage jusqu'à « running ». `user_data(mac)` produit
    le cloud-init ; `on_created(server_id)` est appelé dès la création (pour pouvoir détruire en cas d'échec)."""
    image = scw.image_id(zone, t["image_label"], t["commercial_type"])
    s = scw.create(zone, name, t["commercial_type"], image, t["disque_go"], tags)
    if on_created:
        on_created(s["id"])
    if s.get("state") not in (None, "stopped"):
        raise ScalewayError(f"instance créée dans l'état « {s.get('state')} » au lieu de « stopped »")
    nic = scw.attach_private_network(zone, s["id"], private_network_id, sleep=sleep)
    scw.set_cloud_init(zone, s["id"], user_data(nic["mac_address"]))
    scw.start(zone, s["id"], sleep=sleep)
    return {"server_id": s["id"], "mac": nic["mac_address"], "nic": nic["id"]}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Orchestration (psycopg, conteneur taches)

def launch(conn, scw: Scaleway, rules: dict, tache: str, parametres: dict, env: dict, now: datetime | None = None,
           sleep=time.sleep, log=print) -> dict:
    """Crée un travailleur, si les plafonds et le verrou le permettent. Retourne {"travailleur": id}, {"refus": motif}
    ou {"occupe": id} (un travailleur de cette tâche est déjà actif). Une étape en échec lève l'erreur, après avoir
    détruit l'instance (garde de Scaleway.destroy) et clos la ligne."""
    now = now or datetime.now(timezone.utc)
    p, t = rules["travailleurs"], type_params(rules, tache)
    today = [dict(zip(("cree_le", "fini_le"), r)) for r in conn.execute(
        "SELECT cree_le, fini_le FROM travailleurs WHERE cree_le >= %s", (now.replace(hour=0, minute=0, second=0, microsecond=0),)).fetchall()]
    refus = quota(rules, today, now)
    if refus:
        return {"refus": refus}
    ip, pn = os.environ.get("MARS_IP_PRIVEE"), os.environ.get("SCW_PRIVATE_NETWORK_ID")
    if not ip or not pn:
        return {"refus": "réseau privé non configuré (MARS_IP_PRIVEE, SCW_PRIVATE_NETWORK_ID)"}
    retour = f"http://{ip}:8090/api/travailleurs"           # nginx, port réservé au retour (web/nginx.conf)
    token = secrets.token_urlsafe(32)
    zone = p["zone"]
    # Verrou : index unique sur « verrou » parmi les travailleurs non détruits. L'insertion est atomique ; un second
    # lancement concurrent (rattrapage automatique et commande manuelle) échoue ici, avant tout appel à Scaleway.
    import psycopg
    try:
        with conn.transaction():
            wid = conn.execute(
                "INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, verrou) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (tache, t["commercial_type"], zone, token_hash(token), json.dumps(parametres), tache)).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        busy = conn.execute("SELECT id FROM travailleurs WHERE verrou = %s AND detruit_le IS NULL", (tache,)).fetchone()
        return {"occupe": busy[0] if busy else None}
    created: list[str] = []

    def on_created(server_id):
        created.append(server_id)
        conn.execute("UPDATE travailleurs SET scw_server_id = %s WHERE id = %s", (server_id, wid))
    try:
        task = {**parametres, "retour": f"{retour.rstrip('/')}/{wid}", "jeton": token, "script": t["script"]}
        script = (ROOT / t["script"]).read_text()
        r = provision(scw, zone, f"mars-travailleur-{wid}", t, [TAG, f"run-{wid}", f"tache-{tache}"], pn,
                      lambda mac: cloud_init(script, task, env, t["image"], mac, gpu="gpu" in t["image_label"]),
                      sleep=sleep, on_created=on_created)
        conn.execute("UPDATE travailleurs SET etat = 'cree' WHERE id = %s", (wid,))
        return {"travailleur": wid, "instance": r["server_id"], "type": t["commercial_type"], "mac_privee": r["mac"]}
    except Exception as e:
        motif = f"lancement : {e}"[:1000]
        conn.execute("UPDATE travailleurs SET etat = 'echec', erreur = %s, fini_le = now() WHERE id = %s", (motif, wid))
        gone = True
        for server_id in created:                     # destruction aussitôt, par le garde
            try:
                gone = scw.destroy(zone, server_id)
            except Exception as d:
                gone = False
                log(f"travailleur {wid} : destruction après échec impossible ({d}) ; la surveillance réessaiera")
        if gone:
            conn.execute("UPDATE travailleurs SET detruit_le = now() WHERE id = %s", (wid,))
            w = dict(zip(COLS, conn.execute(f"SELECT {', '.join(COLS)} FROM travailleurs WHERE id = %s", (wid,)).fetchone()))
            _journal(conn, w, rules)
        raise


def _journal(conn, w: dict, rules: dict):
    """Résumé d'une exécution dans task_runs (tâche travailleur_<tache>), une fois l'instance détruite."""
    t = type_params(rules, w["tache"])
    minutes = ((w["detruit_le"] or datetime.now(timezone.utc)) - w["cree_le"]).total_seconds() / 60
    euros = cost(minutes, t["prix_heure"], t["facturation_min_min"])
    mesures = {**(w["mesures"] or {}), "minutes_instance": round(minutes, 1), "cout_estime_eur": euros}
    conn.execute("UPDATE travailleurs SET mesures = %s WHERE id = %s", (json.dumps(mesures), w["id"]))
    details = {"travailleur": w["id"], "type": w["commercial_type"], "etat": w["etat"], "erreur": w["erreur"], **mesures}
    conn.execute(
        "INSERT INTO task_runs (task, run_day, started_at, finished_at, status, details) VALUES (%s, %s, %s, now(), %s, %s)",
        (f"travailleur_{w['tache']}", w["cree_le"].date(), w["cree_le"], "ok" if w["etat"] == "termine" else "echec",
         json.dumps(details, default=str)))


COLS = ("id", "tache", "etat", "commercial_type", "zone", "scw_server_id", "resultat", "mesures", "erreur",
        "cree_le", "fini_le", "detruit_le", "parametres", "demarre_le")


def supervise(conn, scw: Scaleway | None, rules: dict, handlers: dict, log=print, now: datetime | None = None) -> dict:
    """Une minute de surveillance : résultats reçus traités (handlers[tache](conn, travailleur)), instances finies ou
    trop vieilles détruites, orphelins détruits, exécutions journalisées."""
    now = now or datetime.now(timezone.utc)
    p = rules["travailleurs"]
    out = {"traites": 0, "detruits": 0, "expires": 0, "orphelins": 0}
    rows = [dict(zip(COLS, r)) for r in conn.execute(
        f"SELECT {', '.join(COLS)} FROM travailleurs WHERE detruit_le IS NULL ORDER BY id").fetchall()]
    for w in rows:
        age_min = (now - w["cree_le"]).total_seconds() / 60
        if w["etat"] == "resultats":
            try:
                # Tout ou rien, et une seule fois : la ligne est verrouillée (un autre processus la saute), relue, et
                # passe à « termine » dans la même transaction que l'écriture des détections
                with conn.transaction():
                    if conn.execute("SELECT 1 FROM travailleurs WHERE id = %s AND etat = 'resultats' "
                                    "FOR UPDATE SKIP LOCKED", (w["id"],)).fetchone() is None:
                        continue
                    summary = handlers[w["tache"]](conn, w)
                    conn.execute("UPDATE travailleurs SET etat = 'termine', fini_le = now(), resultat = NULL, "
                                 "mesures = mesures || %s WHERE id = %s", (json.dumps(summary, default=str), w["id"]))
                w["etat"] = "termine"
                w["mesures"] = {**(w["mesures"] or {}), **summary}
            except Exception as e:
                conn.execute("UPDATE travailleurs SET etat = 'echec', fini_le = now(), erreur = %s WHERE id = %s",
                             (f"traitement : {e}"[:1000], w["id"]))
                w["etat"], w["erreur"] = "echec", str(e)
            out["traites"] += 1
        elif w["etat"] in ("demande", "cree") and w["demarre_le"] is None and age_min > p["delai_demarrage_min"]:
            motif = (f"aucun signe de vie {p['delai_demarrage_min']} min après la création (réseau privé ou démarrage en "
                     "échec ; journal : taches.py journal-travailleur, ou console série Scaleway) : destruction")
            conn.execute("UPDATE travailleurs SET etat = 'echec', fini_le = now(), erreur = %s WHERE id = %s", (motif, w["id"]))
            w["etat"], w["erreur"] = "echec", motif
            out["expires"] += 1
            log(f"travailleur {w['id']} : {motif}")
        elif w["etat"] in ("demande", "cree", "demarre") and age_min > p["duree_max_min"]:
            conn.execute("UPDATE travailleurs SET etat = 'echec', fini_le = now(), erreur = %s WHERE id = %s",
                         (f"durée de vie de {p['duree_max_min']} min dépassée : destruction forcée", w["id"]))
            w["etat"] = "echec"
            out["expires"] += 1
            log(f"travailleur {w['id']} : durée de vie dépassée, destruction forcée")
        if w["etat"] in ("termine", "echec"):
            if not w["scw_server_id"]:
                gone = True
            elif scw is None:                                   # clés absentes : rien n'est marqué détruit
                gone = False
            else:
                try:
                    gone = scw.destroy(w["zone"], w["scw_server_id"])
                except RefusDestruction as e:
                    # Jamais détruite : la ligne est close, sans rien toucher chez Scaleway
                    log(f"travailleur {w['id']} : DESTRUCTION REFUSÉE, {e}")
                    conn.execute("UPDATE travailleurs SET erreur = coalesce(erreur || ' ; ', '') || %s, detruit_le = now() "
                                 "WHERE id = %s", (f"destruction refusée : {e}"[:500], w["id"]))
                    continue
            if gone:
                conn.execute("UPDATE travailleurs SET detruit_le = now() WHERE id = %s", (w["id"],))
                w["detruit_le"] = now
                _journal(conn, w, rules)
                out["detruits"] += 1
    if scw is not None:
        active = {w["scw_server_id"]: w["cree_le"] for w in rows if w["scw_server_id"] and w["etat"] not in ("termine", "echec")}
        for zone in sorted({p["zone"], *(w["zone"] for w in rows)}):
            for sid in orphans(scw.tagged(zone), active, now, p["duree_max_min"], scw.project, scw.protected):
                try:
                    scw.destroy(zone, sid)
                except RefusDestruction as e:
                    log(f"instance {sid} ({zone}) : DESTRUCTION REFUSÉE, {e}")
                    continue
                out["orphelins"] += 1
                log(f"instance orpheline {sid} ({zone}) : destruction")
    return out

