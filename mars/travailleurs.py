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

from mars import traces
from mars.config import ROOT

API = "https://api.scaleway.com"
TAG = "mars-c2-travailleur"
# Groupe de sécurité des travailleurs (lot Z1), un par zone (les groupes de Scaleway sont zonaux) : tout refusé en
# entrée sur l'adresse publique, sortie libre (images, données, réseau privé pour le retour), à état (les réponses aux
# connexions sortantes passent). Créé au besoin par Scaleway.groupe_securite (taches.py sentinel-diagnostic, ou au
# premier lancement), vérifié à chaque création d'instance.
GROUPE_SECURITE = "mars-travailleurs"
NAME = re.compile(r"^mars-travailleur-(\d+)$")
METADATA = "http://169.254.42.42/conf?format=json"     # métadonnées de l'instance courante (Scaleway, sans jeton)
GONE = object()


class ScalewayError(RuntimeError):
    pass


def en_rupture(e: Exception | str) -> bool:
    """Rupture de stock chez Scaleway (412 « out_of_stock » à la création ou au démarrage, essai du 16/10/2026)."""
    t = str(e).lower()
    return "out_of_stock" in t or "out of stock" in t


class RefusDestruction(ScalewayError):
    """Destruction refusée : l'instance n'est pas un travailleur."""


class RefusLancement(Exception):
    """Lancement refusé pour un motif attendu (plafond quotidien, clés ou réseau privé absents) : la tâche est en échec
    avec ce motif, sans trace d'erreur Python."""
    motif_seul = True


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

    def resolve_image(self, zone: str, label: str, commercial_type: str, arch: str = "x86_64",
                      volume: str = "l_ssd") -> dict:
        """Image de démarrage : catalogue Scaleway (libellé, zone, type d'instance, architecture, type de stockage),
        puis fiche de l'image dans l'API Instances pour son volume racine. Retourne {"id", "name", "arch", "root_type",
        "root_size", "fiche"} (`root_type` : type du volume de l'instance ; `fiche` : réponse brute de l'API, pour le
        diagnostic) ; ScalewayError avec le motif précis sinon.

        * Disque local (« l_ssd », instances CPU) : volume racine de l'image local, taille donnée par la fiche. Inchangé
          depuis le premier essai réel (08/10/2026).
        * Volume bloc (« sbs_volume », seul stockage des GPU L4 ; image du catalogue de type « instance_sbs ») : le
          volume racine de l'image est un instantané du stockage bloc (« sbs_snapshot ») ; si la fiche de l'API
          Instances n'en donne pas la taille, elle est lue dans l'API Block Storage (instantané), comme le fait la
          ligne de commande Scaleway. Confirmé par `taches.py sentinel-diagnostic` le 13/10/2026 : fiche de l'image
          GPU avec un volume racine « sbs_snapshot » de taille 0, instantané de 20 Go dans l'API Block Storage ; c'est
          cette taille nulle qui faisait refuser l'image les 11 et 12/10/2026."""
        kind = "instance_sbs" if volume == "sbs_volume" else "instance_local"
        d = self._req("GET", f"/marketplace/v2/local-images?image_label={label}&zone={zone}&type={kind}")
        found = [i for i in (d.get("local_images", []) if d is not GONE else [])
                 if commercial_type in i.get("compatible_commercial_types", []) and i.get("arch") == arch
                 and i.get("type", "instance_local") == kind]
        if not found:
            what = "en stockage bloc" if volume == "sbs_volume" else "sur disque local"
            raise ScalewayError(f"aucune image « {label} » {arch} {what} pour {commercial_type} en {zone} au catalogue")
        iid = found[0]["id"]
        img = self._req("GET", f"/instance/v1/zones/{zone}/images/{iid}")
        if img is GONE or not (img or {}).get("image"):
            raise ScalewayError(f"image {iid} ({label}) du catalogue introuvable dans l'API Instances en {zone}")
        img = img["image"]
        root = img.get("root_volume") or {}
        if img.get("arch") != arch:
            raise ScalewayError(f"image {iid} ({label}) : architecture {img.get('arch')}, {arch} attendue")
        if not root.get("id"):
            raise ScalewayError(f"image {iid} ({label}) sans volume racine dans sa fiche : {_short(img)}")
        if volume != "sbs_volume":
            if root.get("volume_type") != "l_ssd":
                raise ScalewayError(f"image {img['id']} : volume racine {root.get('volume_type')}, disque local attendu")
            if not root.get("size"):
                raise ScalewayError(f"image {iid} ({label}) : taille du volume racine absente de sa fiche")
            size = int(root["size"])
        else:
            if root.get("volume_type") not in ("sbs_snapshot", "sbs_volume", None):
                raise ScalewayError(f"image {iid} : volume racine {root.get('volume_type')}, instantané bloc attendu")
            size = int(root.get("size") or 0) or self.block_snapshot_size(zone, root["id"])
            if not size:
                raise ScalewayError(f"image {iid} ({label}) : taille du volume racine inconnue (fiche de l'image et "
                                    f"instantané bloc {root['id']}) : {_short(img)}")
        return {"id": img["id"], "name": img.get("name"), "arch": img["arch"], "root_type": volume,
                "root_size": size, "fiche": img}

    def availability(self, zone: str) -> dict[str, str]:
        """Disponibilité de chaque type d'instance de la zone : « available », « scarce » ou « shortage » (API Instances,
        lecture seule, aucune création)."""
        out, page = {}, 1
        while True:
            d = self._req("GET", f"/instance/v1/zones/{zone}/products/servers/availability?per_page=100&page={page}")
            servers = {} if d is GONE else (d or {}).get("servers", {})
            out.update({k: (v or {}).get("availability", "inconnue") for k, v in servers.items()})
            if len(servers) < 100:
                return out
            page += 1

    def products(self, zone: str) -> dict[str, dict]:
        """Catalogue des types d'instance de la zone : prix horaire, processeurs, mémoire, GPU (lecture seule)."""
        out, page = {}, 1
        while True:
            d = self._req("GET", f"/instance/v1/zones/{zone}/products/servers?per_page=100&page={page}")
            servers = {} if d is GONE else (d or {}).get("servers", {})
            out.update(servers)
            if len(servers) < 100:
                return out
            page += 1

    def block_volume(self, zone: str, volume_id: str):
        """Fiche d'un volume bloc (API Block Storage) ; None s'il n'existe pas (404). ScalewayError sinon."""
        d = self._req("GET", f"/block/v1alpha1/zones/{zone}/volumes/{volume_id}")
        return None if d is GONE else d

    def block_volume_size(self, zone: str, volume_id: str) -> int:
        """Taille d'un volume bloc lue dans l'API Block Storage (l'API Instances renvoie 0 pour un volume « sbs_volume »,
        constaté au premier essai réel du 14/10/2026), 0 si illisible."""
        try:
            d = self.block_volume(zone, volume_id)
        except ScalewayError:
            return 0
        return int((d or {}).get("size") or 0)

    def block_snapshot_size(self, zone: str, snapshot_id: str) -> int:
        """Taille d'un instantané du stockage bloc (API Block Storage), 0 si illisible."""
        try:
            d = self._req("GET", f"/block/v1alpha1/zones/{zone}/snapshots/{snapshot_id}")
        except ScalewayError:
            return 0
        return 0 if d is GONE or not d else int(d.get("size") or 0)

    def groupe_securite(self, zone: str, creer: bool = True) -> dict:
        """Groupe de sécurité des travailleurs dans la zone : {"id", "cree"} ; créé s'il manque (si `creer`). Refus
        (ScalewayError) si un groupe du même nom accepte une entrée : quelqu'un l'aurait modifié."""
        d = self._req("GET", f"/instance/v1/zones/{zone}/security_groups?project={self.project}&name={GROUPE_SECURITE}")
        groupes = [g for g in ((d or {}).get("security_groups", []) if d is not GONE else [])
                   if g.get("name") == GROUPE_SECURITE and g.get("project") == self.project]
        if groupes:
            g = groupes[0]
            r = self._req("GET", f"/instance/v1/zones/{zone}/security_groups/{g['id']}/rules?page_size=100")
            motif = probleme_groupe(g, (r or {}).get("rules", []) if r is not GONE else [])
            if motif:
                raise ScalewayError(f"groupe de sécurité {GROUPE_SECURITE} en {zone} : {motif}")
            return {"id": g["id"], "cree": False}
        if not creer:
            raise ScalewayError(f"groupe de sécurité {GROUPE_SECURITE} absent en {zone}")
        g = self._req("POST", f"/instance/v1/zones/{zone}/security_groups", json={
            "name": GROUPE_SECURITE, "project": self.project, "stateful": True, "enable_default_security": True,
            "inbound_default_policy": "drop", "outbound_default_policy": "accept", "tags": [TAG],
            "description": "Travailleurs éphémères de MARS C2 : entrée refusée, sortie libre"})["security_group"]
        return {"id": g["id"], "cree": True}

    def create(self, zone: str, name: str, commercial_type: str, image: dict, disque_go: int, tags: list,
               security_group: str | None = None) -> dict:
        """Crée l'instance éteinte à partir de l'image : le volume racine (index 0) n'est décrit que par sa taille et son
        type, ceux de l'image (comme le fait la ligne de commande Scaleway) ; il est ainsi copié de l'image, agrandi à
        `disque_go` au besoin. Refuse toute création sans image résolue."""
        if not image or not image.get("id") or image.get("root_type") not in ("l_ssd", "sbs_volume") or not image.get("root_size"):
            raise ScalewayError("création refusée : aucune image de démarrage résolue")
        size = max(int(disque_go * 1e9), image["root_size"])
        body = {"name": name, "project": self.project, "commercial_type": commercial_type, "image": image["id"],
                "dynamic_ip_required": True, "tags": tags, "boot_type": "local",
                "volumes": {"0": {"size": size, "volume_type": image["root_type"]}}}
        if security_group:
            body["security_group"] = security_group
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
        with traces.sondage("scaleway.attente_reseau_prive", **{"scaleway.zone": zone}) as so:
            so.etat(nic.get("state"))
            while nic.get("state") != "available":
                if nic.get("state") in ("syncing_error", "error"):
                    raise ScalewayError(f"rattachement au réseau privé en erreur (état {nic.get('state')})")
                if waited >= wait_s:
                    raise ScalewayError(f"rattachement au réseau privé non prêt après {wait_s:.0f} s "
                                        f"(état {nic.get('state')})")
                sleep(3)
                waited += 3
                with traces.sans_instrumentation():
                    d = self._req("GET", f"/instance/v1/zones/{zone}/servers/{server_id}/private_nics/{nic['id']}")
                nic = d["private_nic"] if d is not GONE else {"state": "error"}
                so.etat(nic.get("state"))
        if nic.get("private_network_id") not in (None, private_network_id):
            raise ScalewayError("carte rattachée à un autre réseau privé")
        return nic

    def start(self, zone: str, server_id: str, wait_s: float = 300, sleep=time.sleep):
        """Démarre l'instance et attend l'état « running » ; ScalewayError si elle s'arrête ou tarde."""
        self.action(zone, server_id, "poweron")              # requête qui change quelque chose : son propre span
        waited = 0.0
        with traces.sondage("scaleway.attente_demarrage", **{"scaleway.zone": zone}) as so:
            while True:
                with traces.sans_instrumentation():          # un span pour toute l'attente, pas un par GET
                    s = self.server(zone, server_id)
                state = (s or {}).get("state")
                so.etat(state)
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

    def diagnose(self, zone: str, server_id: str) -> dict:
        """État d'une instance pour comprendre un travailleur muet : état, image, démarrage, volume de démarrage, cartes
        du réseau privé avec leur adresse (API IPAM ; « inconnue » si la clé n'y a pas droit). Jamais d'erreur levée."""
        out: dict = {"releve_le": datetime.now(timezone.utc).isoformat()}
        try:
            s = self.server(zone, server_id)
        except Exception as e:
            return {**out, "erreur": f"lecture de l'instance impossible : {e}"[:300]}
        if s is None:
            return {**out, "etat": "introuvable"}
        root = (s.get("volumes") or {}).get("0") or {}
        if root.get("volume_type") == "sbs_volume" and root.get("id"):
            root = {**root, "size": self.block_volume_size(zone, root["id"]) or root.get("size")}
        out.update({"etat": s.get("state"), "detail_etat": s.get("state_detail"), "type": s.get("commercial_type"),
                    "image": (s.get("image") or {}).get("name"), "image_id": (s.get("image") or {}).get("id"),
                    "demarrage": s.get("boot_type"),
                    "volume_demarrage": {k: root.get(k) for k in ("id", "volume_type", "size", "state")} if root else None,
                    "ipv4_publique": (s.get("public_ip") or {}).get("address")})
        nics = []
        for n in s.get("private_nics") or []:
            nic = {k: n.get(k) for k in ("id", "state", "mac_address", "private_network_id")}
            try:
                d = self._req("GET", f"/ipam/v1/regions/{zone.rsplit('-', 1)[0]}/ips?resource_id={n['id']}"
                                     "&resource_type=instance_private_nic")
                ips = [] if d is GONE else [i.get("address") for i in d.get("ips", [])]
                nic["adresses"] = ips or ["aucune attribuée"]
            except Exception as e:
                nic["adresses"] = [f"inconnue ({str(e)[:120]})"]
            nics.append(nic)
        out["cartes_privees"] = nics or "aucune carte sur le réseau privé"
        return out

    def tagged(self, zone: str) -> list[dict]:
        """Travailleurs de la zone : filtre d'étiquette demandé à l'API, puis revérifié ici (garde complète)."""
        d = self._req("GET", f"/instance/v1/zones/{zone}/servers?tags={TAG}&per_page=100&project={self.project}")
        servers = [] if d is GONE else d.get("servers", [])
        return [s for s in servers if refus(s, self.project, self.protected) is None]

    def delete_block_volumes(self, zone: str, ids: list[str]) -> list[str]:
        """Supprime des volumes bloc (API Block Storage) ; retourne ceux qui restent (encore attachés ou en cours de
        détachement : réessayé à la minute suivante). Seuls les volumes enregistrés à la création d'un travailleur sont
        passés ici (colonne travailleurs.volumes_bloc), jamais une liste lue ailleurs."""
        left = []
        for v in ids:
            try:
                self._req("DELETE", f"/block/v1alpha1/zones/{zone}/volumes/{v}")       # 404 : déjà supprimé
            except ScalewayError as e:
                if "in_use" not in str(e) and "precondition" not in str(e) and "409" not in str(e) and "412" not in str(e):
                    raise
                left.append(v)
        return left

    def destroy(self, zone: str, server_id: str, block_volumes: list[str] | None = None) -> bool:
        """Détruit l'instance et ses volumes, après avoir vérifié chez Scaleway que c'est un travailleur (sinon
        RefusDestruction, sans aucun appel destructeur). Vrai quand plus rien n'existe ; faux si la destruction est en
        cours (un nouvel appel, à la minute suivante, la termine). `block_volumes` : volumes bloc enregistrés à la
        création (GPU) ; ils survivent à l'instance chez Scaleway et sont supprimés une fois l'instance partie."""
        if not server_id or server_id in self.protected:
            raise RefusDestruction(f"instance {server_id} protégée : serveur principal")
        s = self.server(zone, server_id)
        if s is None:
            return not self.delete_block_volumes(zone, block_volumes or [])
        motif = refus(s, self.project, self.protected)
        if motif:
            raise RefusDestruction(motif)
        # Séquence de Scaleway, une étape par appel (la surveillance rappelle chaque minute jusqu'à la fin) :
        #   running          : action terminate (instance, volumes locaux et adresse dynamique supprimés d'un coup) ;
        #   stopped in place : éteinte depuis le système mais encore allouée (fin du script du travailleur) : la
        #                      suppression y est refusée (« resource_still_in_use, instance should be powered off ») ;
        #                      action poweroff, puis l'état passe par stopping jusqu'à stopped ;
        #   stopped          : suppression de l'instance, puis de ses volumes ;
        #   starting, stopping, locked… : attendre.
        state = s.get("state")
        action = f"/instance/v1/zones/{zone}/servers/{server_id}/action"
        vols = {v["id"]: v.get("volume_type") for v in (s.get("volumes") or {}).values() if v and v.get("id")}
        block = [v for v, kind in vols.items() if kind == "sbs_volume"]
        if state == "running":
            # terminate supprime l'instance et ses disques locaux, mais laisse les volumes bloc (facturés) : pour
            # une instance à volume bloc, extinction puis suppression explicite
            self._req("POST", action, json={"action": "poweroff" if block else "terminate"})
            return False
        if state == "stopped in place":
            self._req("POST", action, json={"action": "poweroff"})
            return False
        if state == "stopped":
            try:
                self._req("DELETE", f"/instance/v1/zones/{zone}/servers/{server_id}")
            except ScalewayError as e:
                if "still_in_use" not in str(e) and "powered off" not in str(e):
                    raise
                self._req("POST", action, json={"action": "poweroff"})     # pas encore libérée : on recommence
                return False
            for v, kind in vols.items():                         # 404 : déjà supprimé avec l'instance
                if kind != "sbs_volume":
                    self._req("DELETE", f"/instance/v1/zones/{zone}/volumes/{v}")
            return not self.delete_block_volumes(zone, sorted(set(block) | set(block_volumes or [])))
        return False


# Décisions pures

def _short(d: dict, n: int = 400) -> str:
    """Fiche brute abrégée pour un message d'erreur (le diagnostic, lui, l'affiche entière)."""
    return json.dumps(d, ensure_ascii=False, default=str)[:n]


def failure(res: dict) -> str | None:
    """Motif d'échec d'un résultat de travailleur : son erreur déclarée, ou, s'il avait des scènes à traiter et qu'aucune
    n'a abouti, celle de la première (un script plus ancien ne le déclarait pas : essai Sentinel du 14/10/2026)."""
    if res.get("erreur"):
        return res["erreur"]
    scenes = res.get("scenes") or []
    if scenes and all(sc.get("erreur") for sc in scenes):
        return f"aucune scène analysée sur {len(scenes)} : {scenes[0]['erreur']}"
    return None


def type_params(rules: dict, tache: str, variante: dict | None = None) -> dict:
    """Paramètres du type de travailleur `tache`, avec une variante (type de secours, repli sur processeur) qui en
    remplace certains : type d'instance, zone, volume, prix, options Docker."""
    p = rules["travailleurs"]
    t = {k: v for k, v in p["types"][tache].items() if k not in ("secours", "repli_cpu")}
    t.update(variante or {})
    t.setdefault("facturation", p["facturation"])
    return t


FACTURATION = {"minute": 1, "heure_entamee": 60}


def granularite_min(t: dict) -> int:
    """Granularité de facturation d'un type (lot C5) : « minute » (L4, L40S) ou « heure_entamee » (GP1-S, POP2,
    DEV1, RENDER-S : une heure entamée est due en entier, même pour un échec au démarrage)."""
    return FACTURATION[t["facturation"]]


def eprouves(conn, tache: str) -> set:
    """Types (commercial_type, zone) sur lesquels au moins un travailleur a abouti."""
    return {tuple(r) for r in conn.execute("SELECT DISTINCT commercial_type, zone FROM travailleurs WHERE tache = %s "
                                           "AND etat = 'termine'", (tache,)).fetchall()}


def garde_essai(rules: dict, t: dict, eprouves_: set | None, explicite: bool) -> str | None:
    """Garde de dépense (lot C5, essai 26 : RENDER-S facturé 1,23 € pour 0,28 € estimés) : un type facturé à l'heure
    entamée plus cher que prix_max_essai_eur n'est lancé qu'une fois éprouvé (un travailleur abouti), ou sur essai
    explicite (SENTINEL_CALCUL_ESSAI). Retourne le motif du refus, ou None."""
    plafond = rules["travailleurs"]["prix_max_essai_eur"]
    if explicite or t["facturation"] != "heure_entamee" or t["prix_heure"] <= plafond:
        return None
    if (t["commercial_type"], t.get("zone", rules["travailleurs"]["zone"])) in (eprouves_ or set()):
        return None
    return (f"non éprouvé, facturé à l'heure entamée ({t['prix_heure']:.2f} €/h, au delà de {plafond:.2f} €) : "
            "SENTINEL_CALCUL_ESSAI requis").replace(".", ",")


def quota(rules: dict, today: list[dict], now: datetime, tache: str = "viirs") -> str | None:
    """Motif de refus si un plafond quotidien est atteint pour lancer un travailleur `tache`. `today` : travailleurs
    du jour dont une instance a vraiment été créée chez Scaleway (cree_le, fini_le, tache) ; un lancement refusé avant
    toute création (image introuvable, quota, clés) ne coûte rien et ne compte pas. Place réservée : tant que la nuit
    VIIRS du jour n'a pas eu son travailleur, les autres tâches laissent `reserve_viirs` travailleurs et minutes."""
    p = rules["travailleurs"]
    res = p.get("reserve_viirs", {})
    keep_n = keep_min = 0
    if tache != "viirs" and not any(w.get("tache") == "viirs" for w in today):
        keep_n, keep_min = res.get("travailleurs", 0), res.get("minutes", 0)
    if len(today) >= p["max_par_jour"] - keep_n:
        reserve = f", dont {keep_n} réservé à la nuit VIIRS" if keep_n else ""
        return f"plafond de {p['max_par_jour']} travailleurs par jour atteint{reserve}"
    minutes = sum(((w["fini_le"] or now) - w["cree_le"]).total_seconds() / 60 for w in today)
    if minutes >= p["max_minutes_par_jour"] - keep_min:
        reserve = f", dont {keep_min} réservées à la nuit VIIRS" if keep_min else ""
        return f"plafond de {p['max_minutes_par_jour']} minutes par jour atteint ({minutes:.0f} min{reserve})"
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


def cloud_init(script: str, tache: dict, env: dict, image: str, mac: str, docker_opts: str = "",
               traceparent: str | None = None) -> str:
    """Configuration cloud-init : script de démarrage (travailleurs/demarrage.sh), script et tâche du travailleur,
    secrets en 0600 ; `mac` : adresse de la carte du réseau privé, que le démarrage configure explicitement ;
    `docker_opts` : options du conteneur d'analyse (GPU : « --gpus all --shm-size=16g ») ; `traceparent` : contexte W3C
    du lancement (pas un secret), dont le démarrage et l'analyse prolongent la trace (travailleurs/otel_mini.py)."""
    def f(path, content, mode):
        b = base64.b64encode(content.encode()).decode()
        return f"  - path: {path}\n    encoding: b64\n    permissions: '{mode}'\n    content: {b}\n"
    envtxt = "".join(f"{k}={v}\n" for k, v in env.items())
    name = Path(tache.get("script", "travailleur.py")).name
    boot = {"RETOUR": tache["retour"], "JETON": tache["jeton"], "MAC": mac.lower(), "IMAGE": image, "SCRIPT": name,
            "GPU": docker_opts, "TRACEPARENT": traceparent or ""}
    boottxt = "".join(f"{k}={json.dumps(v)}\n" for k, v in boot.items())
    return ("#cloud-config\nwrite_files:\n" + f("/mars/demarrage.sh", (ROOT / "travailleurs" / "demarrage.sh").read_text(), "0755")
            + f("/mars/demarrage.env", boottxt, "0600") + f(f"/mars/{name}", script, "0644")
            + f("/mars/otel_mini.py", (ROOT / "travailleurs" / "otel_mini.py").read_text(), "0644")
            + f("/mars/traces.sh", (ROOT / "travailleurs" / "traces.sh").read_text(), "0644")
            + f("/mars/tache.json", json.dumps(tache), "0600") + f("/mars/env", envtxt, "0600")
            + "runcmd:\n  - [bash, /mars/demarrage.sh]\n")


def boot_problem(server: dict | None, image: dict, block_size=None) -> str | None:
    """Motif si l'instance relue ne démarrera pas sur l'image attendue, sinon None : image de l'instance, démarrage
    sur disque local, volume racine présent, du type de l'image, au moins aussi grand que son volume racine.

    Volume bloc (« sbs_volume », GPU L4) : l'API Instances renvoie une taille 0, la taille étant gérée par l'API Block
    Storage (premier essai réel du 14/10/2026, instance refusée pour « 0 Go »). Elle y est lue par `block_size(id)` ;
    si elle reste illisible, pas de refus sur la taille seule (les autres contrôles tiennent). Disque local (DEV1-M) :
    taille de la fiche de l'instance, contrôle inchangé."""
    if not server:
        return "instance introuvable après sa création"
    if (server.get("image") or {}).get("id") != image["id"]:
        return f"image de l'instance {(server.get('image') or {}).get('id')}, attendue {image['id']} ({image.get('name')})"
    if server.get("boot_type", "local") != "local":
        return f"démarrage « {server.get('boot_type')} » au lieu du disque local"
    root = (server.get("volumes") or {}).get("0")
    if not root:
        return "aucun volume de démarrage"
    if root.get("volume_type") != image["root_type"]:
        return f"volume de démarrage {root.get('volume_type')}, attendu {image['root_type']}"
    size = int(root.get("size") or 0)
    if root.get("volume_type") == "sbs_volume":
        size = (block_size(root["id"]) if block_size and root.get("id") else 0) or size
        if not size:
            return None
    if size < image["root_size"]:
        return f"volume de démarrage de {size / 1e9:.0f} Go, plus petit que l'image ({image['root_size'] / 1e9:.0f} Go)"
    return None


def probleme_groupe(g: dict, regles: list) -> str | None:
    """Motif si le groupe ne refuse pas toute entrée (politique, règle d'entrée acceptée) ou n'est pas à état."""
    if g.get("inbound_default_policy") != "drop":
        return f"politique d'entrée « {g.get('inbound_default_policy')} » au lieu de « drop »"
    if g.get("stateful") is False:
        return "groupe sans état : les réponses aux connexions sortantes seraient refusées"
    ouvertes = [r for r in regles if r.get("direction") == "inbound" and r.get("action") == "accept"
                and r.get("editable", True)]
    if ouvertes:
        return f"{len(ouvertes)} règle(s) d'entrée acceptée(s)"
    return None


def provision(scw: "Scaleway", zone: str, name: str, t: dict, tags: list, private_network_id: str,
              user_data, sleep=time.sleep, on_created=None, on_block_volumes=None) -> dict:
    """Crée un travailleur dans l'ordre sûr, chaque étape vérifiée : instance éteinte, réseau privé rattaché et prêt,
    cloud-init (qui reçoit l'adresse MAC de la carte privée), démarrage jusqu'à « running ». `user_data(mac)` produit
    le cloud-init ; `on_created(server_id)` est appelé dès la création (pour pouvoir détruire en cas d'échec),
    `on_block_volumes(ids)` dès que les volumes bloc sont connus (pour les supprimer à coup sûr)."""
    with traces.span("scaleway.image", **{"scaleway.zone": zone}):
        image = scw.resolve_image(zone, t["image_label"], t["commercial_type"], t.get("arch", "x86_64"),
                                  t.get("volume", "l_ssd"))
    with traces.span("scaleway.groupe_securite", **{"scaleway.zone": zone}) as sp:
        groupe = scw.groupe_securite(zone)
        sp.set_attribute("scaleway.groupe_cree", groupe["cree"])
    with traces.span("scaleway.creation", **{"scaleway.type": t["commercial_type"]}):
        s = scw.create(zone, name, t["commercial_type"], image, t["disque_go"], tags, security_group=groupe["id"])
    if on_created:
        on_created(s["id"])
    block = [v["id"] for v in (s.get("volumes") or {}).values()
             if v and v.get("id") and v.get("volume_type") == "sbs_volume"]
    if block and on_block_volumes:
        on_block_volumes(block)
    if s.get("state") not in (None, "stopped"):
        raise ScalewayError(f"instance créée dans l'état « {s.get('state')} » au lieu de « stopped »")
    # Relecture : l'instance doit démarrer sur le disque local copié de l'image attendue (sinon « UEFI Interactive
    # Shell » à la console : aucun système amorçable, constaté au premier essai réel)
    with traces.span("scaleway.verification_demarrage"):
        relue = scw.server(zone, s["id"])
        motif = boot_problem(relue, image, lambda vid: scw.block_volume_size(zone, vid))
    if motif:
        raise ScalewayError(f"volume de démarrage incorrect : {motif}")
    sg = ((relue or {}).get("security_group") or {}).get("id")
    if sg != groupe["id"]:
        raise ScalewayError(f"instance rattachée au groupe de sécurité {sg}, attendu {groupe['id']} ({GROUPE_SECURITE})")
    with traces.span("scaleway.reseau_prive"):
        nic = scw.attach_private_network(zone, s["id"], private_network_id, sleep=sleep)
    with traces.span("scaleway.cloud_init"):
        scw.set_cloud_init(zone, s["id"], user_data(nic["mac_address"]))
    with traces.span("scaleway.demarrage"):
        scw.start(zone, s["id"], sleep=sleep)
    return {"server_id": s["id"], "mac": nic["mac_address"], "nic": nic["id"]}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Orchestration (psycopg, conteneur taches)

def launch(conn, scw: Scaleway, rules: dict, tache: str, parametres: dict, env: dict, now: datetime | None = None,
           sleep=time.sleep, log=print, variante: dict | None = None) -> dict:
    """Crée un travailleur, si les plafonds et le verrou le permettent. Retourne {"travailleur": id}, {"refus": motif}
    ou {"occupe": id} (un travailleur de cette tâche est déjà actif). Une étape en échec lève l'erreur, après avoir
    détruit l'instance (garde de Scaleway.destroy) et clos la ligne.

    Trace : span « travailleur.lancement », dont le contexte (traceparent) est enregistré dans la ligne et remis au
    travailleur ; son démarrage, ses téléchargements, son inférence, le traitement de ses résultats et sa destruction
    s'y rattachent : une analyse est une seule trace de bout en bout."""
    with traces.span("travailleur.lancement", **{"travailleur.tache": tache,
                                                 "travailleur.type": type_params(rules, tache, variante)["commercial_type"],
                                                 "travailleur.zone": type_params(rules, tache, variante).get(
                                                     "zone", rules["travailleurs"]["zone"])}) as sp:
        r = _launch(conn, scw, rules, tache, parametres, env, now, sleep, log, variante)
        sp.set_attributes(traces.propres({f"travailleur.{k}": v for k, v in r.items() if k != "mac_privee"}))
        if "refus" in r:
            sp.set_attribute("travailleur.refus", r["refus"])
        return r


def _launch(conn, scw, rules, tache, parametres, env, now, sleep, log, variante=None) -> dict:
    now = now or datetime.now(timezone.utc)
    p, t = rules["travailleurs"], type_params(rules, tache, variante)
    # Seuls comptent les travailleurs dont une instance a été créée chez Scaleway (scw_server_id renseigné dès la
    # création) : un lancement refusé avant (image, quota, clés) ne coûte rien et n'entame pas le plafond
    today = [dict(zip(("cree_le", "fini_le", "tache"), r)) for r in conn.execute(
        "SELECT cree_le, fini_le, tache FROM travailleurs WHERE cree_le >= %s AND scw_server_id IS NOT NULL "
        "AND NOT rupture_stock",
        (now.replace(hour=0, minute=0, second=0, microsecond=0),)).fetchall()]
    refus = quota(rules, today, now, tache)
    if refus:
        return {"refus": refus}
    ip, pn = os.environ.get("MARS_IP_PRIVEE"), os.environ.get("SCW_PRIVATE_NETWORK_ID")
    if not ip or not pn:
        return {"refus": "réseau privé non configuré (MARS_IP_PRIVEE, SCW_PRIVATE_NETWORK_ID)"}
    retour = f"http://{ip}:8090/api/travailleurs"           # nginx, port réservé au retour (web/nginx.conf)
    token = secrets.token_urlsafe(32)
    zone = t.get("zone", p["zone"])            # GPU L4 : fr-par-2 ; le réseau privé est régional (fr-par)
    # Verrou : index unique sur « verrou » parmi les travailleurs non détruits. L'insertion est atomique ; un second
    # lancement concurrent (rattrapage automatique et commande manuelle) échoue ici, avant tout appel à Scaleway.
    import psycopg
    try:
        with conn.transaction():
            wid = conn.execute(
                "INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, verrou, trace_id, "
                "traceparent, duree_max_min, cout_prevu_eur, facturation) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s) RETURNING id",
                (tache, t["commercial_type"], zone, token_hash(token), json.dumps(parametres), tache,
                 traces.trace_id(), traces.traceparent(), t.get("duree_max_min"), t.get("cout_prevu_eur"),
                 t["facturation"])).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        busy = conn.execute("SELECT id FROM travailleurs WHERE verrou = %s AND detruit_le IS NULL", (tache,)).fetchone()
        return {"occupe": busy[0] if busy else None}
    created: list[str] = []

    def on_created(server_id):
        created.append(server_id)
        conn.execute("UPDATE travailleurs SET scw_server_id = %s WHERE id = %s", (server_id, wid))

    def on_block_volumes(ids):
        block.extend(ids)
        conn.execute("UPDATE travailleurs SET volumes_bloc = %s WHERE id = %s", (ids, wid))
    block: list[str] = []
    try:
        task = {"retour": f"{retour.rstrip('/')}/{wid}", "jeton": token, "script": t["script"]}
        if t.get("tache_par_le_reseau"):
            # La tâche (et, pour Sentinel, la paire de clés Copernicus dédiée aux travailleurs) est remise une seule
            # fois par le réseau privé, au travailleur qui présente le jeton (backend/travailleurs.py) : rien de
            # secret dans le cloud-init, ni dans un journal, ni en base
            task["tache_par_le_reseau"] = True
        else:
            task = {**parametres, **task}
        script = (ROOT / t["script"]).read_text()
        r = provision(scw, zone, f"mars-travailleur-{wid}", t, [TAG, f"run-{wid}", f"tache-{tache}"], pn,
                      lambda mac: cloud_init(script, task, env, t["image"], mac, t.get("docker_opts", ""),
                                             traces.traceparent()),
                      sleep=sleep, on_created=on_created, on_block_volumes=on_block_volumes)
        conn.execute("UPDATE travailleurs SET etat = 'cree' WHERE id = %s", (wid,))
        return {"travailleur": wid, "instance": r["server_id"], "type": t["commercial_type"], "mac_privee": r["mac"]}
    except Exception as e:
        rupture = en_rupture(e)
        motif = (f"rupture de stock chez Scaleway ({t['commercial_type']}, {zone})" if rupture else f"lancement : {e}")[:1000]
        # Rupture de stock : instance et volume bloc supprimés aussitôt (ci dessous), travailleur hors du plafond
        conn.execute("UPDATE travailleurs SET etat = 'echec', erreur = %s, fini_le = now(), rupture_stock = %s "
                     "WHERE id = %s", (motif, rupture, wid))
        gone = True
        for server_id in created:                     # destruction aussitôt, par le garde
            try:
                gone = scw.destroy(zone, server_id, block)
            except Exception as d:
                gone = False
                log(f"travailleur {wid} : destruction après échec impossible ({d}) ; la surveillance réessaiera")
        if gone:
            conn.execute("UPDATE travailleurs SET detruit_le = now() WHERE id = %s", (wid,))
            w = dict(zip(COLS, conn.execute(f"SELECT {', '.join(COLS)} FROM travailleurs WHERE id = %s", (wid,)).fetchone()))
            _journal(conn, w, rules)
        if rupture:
            traces.attributs(**{"travailleur.rupture_stock": True})
            log(f"travailleur {wid} : {motif}, instance {'détruite' if gone else 'en cours de destruction'}")
            return {"rupture": motif, "travailleur": wid, "type": t["commercial_type"], "zone": zone}
        raise


def variantes(rules: dict, tache: str) -> list[dict]:
    """Variantes d'un type de travailleur, dans l'ordre : type de base, types de secours, types sur processeur (avec
    « calcul » : gpu ou cpu, pour Sentinel ; un type sans secours n'a que sa base). `repli_cpu` : une liste depuis le
    lot C5 (GP1-S, puis POP2-8C-32G en secours), un seul type avant."""
    base = rules["travailleurs"]["types"][tache]
    out = [{"calcul": "gpu" if "--gpus" in base.get("docker_opts", "") else "cpu"}]
    out += [{**v, "calcul": "gpu"} for v in base.get("secours", [])]
    cpu = base.get("repli_cpu") or []
    out += [{**v, "calcul": "cpu"} for v in (cpu if isinstance(cpu, list) else [cpu])]
    return out


def variante_de(rules: dict, tache: str, commercial_type: str | None, zone: str | None) -> dict | None:
    """Variante qui correspond au type et à la zone d'un travailleur lancé (prix réel au journal)."""
    for v in variantes(rules, tache):
        t = type_params(rules, tache, v)
        if t["commercial_type"] == commercial_type and t.get("zone", rules["travailleurs"]["zone"]) == zone:
            return v
    return None


def _journal(conn, w: dict, rules: dict):
    """Résumé d'une exécution dans task_runs (tâche travailleur_<tache>), une fois l'instance détruite. Prix du type
    réellement lancé ; une instance détruite pour rupture de stock n'a jamais tourné : coût nul."""
    t = type_params(rules, w["tache"], variante_de(rules, w["tache"], w.get("commercial_type"), w.get("zone")))
    if w.get("facturation"):
        t["facturation"] = w["facturation"]           # mode connu au lancement
    minutes = ((w["detruit_le"] or datetime.now(timezone.utc)) - w["cree_le"]).total_seconds() / 60
    # Coût réel (lot C5) : de la création à la destruction, selon le mode de facturation du type ; une instance
    # détruite pour rupture de stock n'a jamais démarré, rien n'est facturé
    euros = 0.0 if w.get("rupture_stock") else cost(minutes, t["prix_heure"], granularite_min(t))
    prevu = w.get("cout_prevu_eur")
    mesures = {**(w["mesures"] or {}), "minutes_instance": round(minutes, 1), "cout_estime_eur": euros,
               "cout_reel_eur": euros, "facturation": t["facturation"], "cout_prevu_eur": prevu}
    with traces.span("travailleur.cout", parent=w.get("traceparent"),
                     **{"travailleur.id": w["id"], "travailleur.type": w.get("commercial_type"),
                        "travailleur.facturation": t["facturation"], "travailleur.minutes": round(minutes, 1),
                        "travailleur.cout_reel": euros, "travailleur.cout_prevu": prevu,
                        "travailleur.ecart_cout": None if prevu is None else round(euros - float(prevu), 4)}):
        pass
    conn.execute("UPDATE travailleurs SET mesures = %s, cout_reel_eur = %s WHERE id = %s",
                 (json.dumps(mesures), euros, w["id"]))
    details = {"travailleur": w["id"], "type": w["commercial_type"], "etat": w["etat"], "erreur": w["erreur"], **mesures}
    if w.get("volumes_bloc"):
        details["volumes_bloc_supprimes"] = list(w["volumes_bloc"])        # journalisé une fois l'instance détruite
    if w.get("rupture_stock"):          # attendue : ni échec de tâche (alerte « deux échecs de suite »), ni coût
        details.update({"refus": True, "rupture_stock": True})
    if (w.get("mesures") or {}).get("fin_signalee") == "gpu_inutilisable":   # alerte par le marquage seulement
        details.update({"refus": True, "gpu_inutilisable": True})
    conn.execute(
        "INSERT INTO task_runs (task, run_day, started_at, finished_at, status, details, trace_id) "
        "VALUES (%s, %s, %s, now(), %s, %s, %s)",
        (f"travailleur_{w['tache']}", w["cree_le"].date(), w["cree_le"], "ok" if w["etat"] == "termine" else "echec",
         json.dumps(details, default=str), traces.trace_de(w.get("traceparent"))))


COLS = ("id", "tache", "etat", "commercial_type", "zone", "scw_server_id", "resultat", "mesures", "erreur",
        "cree_le", "fini_le", "detruit_le", "parametres", "demarre_le", "volumes_bloc", "traceparent", "rupture_stock",
        "duree_max_min", "cout_prevu_eur", "facturation")


def supervise(conn, scw: Scaleway | None, rules: dict, handlers: dict, log=print, now: datetime | None = None) -> dict:
    """Une minute de surveillance : résultats reçus traités (handlers[tache](conn, travailleur)), instances finies ou
    trop vieilles détruites, orphelins détruits, exécutions journalisées."""
    now = now or datetime.now(timezone.utc)
    p = rules["travailleurs"]
    out = {"traites": 0, "detruits": 0, "expires": 0, "orphelins": 0}
    rows = [dict(zip(COLS, r)) for r in conn.execute(
        f"SELECT {', '.join(COLS)} FROM travailleurs WHERE detruit_le IS NULL ORDER BY id").fetchall()]
    for w in rows:
        _surveiller(conn, scw, rules, handlers, log, now, p, w, out)
    if scw is not None:
        active = {w["scw_server_id"]: w["cree_le"] for w in rows if w["scw_server_id"] and w["etat"] not in ("termine", "echec")}
        zones = {p["zone"], *(t.get("zone", p["zone"]) for t in p["types"].values()), *(w["zone"] for w in rows)}
        for zone in sorted(zones):
            vie = max([p["duree_max_min"]] + [type_params(rules, k, v).get("duree_max_min", 0)
                                              for k in p["types"] for v in variantes(rules, k)]
                      + [w["duree_max_min"] or 0 for w in rows])
            for sid in orphans(scw.tagged(zone), active, now, vie, scw.project, scw.protected):
                with traces.span("travailleur.orphelin", **{"scaleway.zone": zone}):
                    try:
                        scw.destroy(zone, sid)
                    except RefusDestruction as e:
                        traces.erreur(f"destruction refusée : {e}")
                        log(f"instance {sid} ({zone}) : DESTRUCTION REFUSÉE, {e}")
                        continue
                    except ScalewayError as e:
                        traces.erreur(f"destruction à reprendre : {e}")
                        log(f"instance orpheline {sid} ({zone}) : destruction à reprendre, {e}")
                        continue
                out["orphelins"] += 1
                log(f"instance orpheline {sid} ({zone}) : destruction")
    return out


def _surveiller(conn, scw, rules: dict, handlers: dict, log, now: datetime, p: dict, w: dict, out: dict):
    """Un travailleur pendant une minute de surveillance. Chaque action (résultats, expiration, destruction) est un
    span rattaché à la trace du lancement (w["traceparent"]) : une analyse reste une seule trace."""
    tp, attrs = w.get("traceparent"), {"travailleur.id": w["id"], "travailleur.tache": w["tache"]}
    age_min = (now - w["cree_le"]).total_seconds() / 60
    # Durée de vie calculée au lancement (processeur : selon le nombre de fenêtres, lot C5), sinon celle du type
    # réellement lancé, sinon celle de tous
    duree_max = w.get("duree_max_min") or type_params(
        rules, w["tache"], variante_de(rules, w["tache"], w.get("commercial_type"), w.get("zone"))
    ).get("duree_max_min", p["duree_max_min"])
    mesures = w.get("mesures") or {}
    # Lot C4 : fin signalée par le travailleur (script de démarrage), ou instance arrêtée d'elle même : traitée tout de
    # suite (destruction à cette minute même), sans attendre la durée de vie
    if w["etat"] == "gpu_inutilisable":
        with traces.span("travailleur.gpu_inutilisable", parent=tp, **{**attrs, "travailleur.type": w.get("commercial_type"),
                                                                     "travailleur.zone": w.get("zone")}):
            traces.erreur(w.get("erreur") or "GPU inutilisable")
            marquer_incompatible(conn, rules, w, log)
            remettre_en_attente(conn, w, f"GPU inutilisable sur {w.get('commercial_type')} ({w.get('zone')}) : type "
                                         "marqué incompatible, type suivant au prochain essai")
            conn.execute("UPDATE travailleurs SET etat = 'echec' WHERE id = %s", (w["id"],))
        w["etat"] = "echec"
    elif w["etat"] == "echec" and mesures.get("fin_signalee") == "echec" and not mesures.get("echec_traite"):
        _echec_signale(conn, w, tp, attrs, mesures.get("etape_echec") or "", w.get("erreur") or "échec signalé")
    elif (w["etat"] in ("cree", "demarre") and scw is not None and w["scw_server_id"] and age_min > 3
          and _arretee(scw, w)):
        motif = "instance arrêtée d'elle même sans résultat (sortie du script de démarrage en échec)"
        conn.execute("UPDATE travailleurs SET etat = 'echec', fini_le = now(), erreur = coalesce(erreur, %s) "
                     "WHERE id = %s", (motif, w["id"]))
        w["etat"], w["erreur"] = "echec", w.get("erreur") or motif
        _echec_signale(conn, w, tp, attrs, "machine", motif)
    elif w["etat"] == "resultats":
        with traces.span("travailleur.resultats", parent=tp, **attrs), traces.en_base(conn):
            if not _resultats(conn, handlers, w):
                return
        out["traites"] += 1
    elif w["etat"] in ("demande", "cree") and w["demarre_le"] is None and age_min > p["delai_demarrage_min"]:
        motif = (f"aucun signe de vie {p['delai_demarrage_min']} min après la création (réseau privé ou démarrage en "
                 "échec ; diagnostic : taches.py journal-travailleur, ou console série Scaleway) : destruction")
        with traces.span("travailleur.expiration", parent=tp, **attrs):
            traces.erreur(motif)
            # État Scaleway relevé avant la destruction : sans lui, un travailleur muet ne laisse aucune trace
            diag = scw.diagnose(w["zone"], w["scw_server_id"]) if scw is not None and w["scw_server_id"] else None
            conn.execute("UPDATE travailleurs SET etat = 'echec', fini_le = now(), erreur = %s, diagnostic = %s "
                         "WHERE id = %s", (motif, json.dumps(diag) if diag else None, w["id"]))
        w["etat"], w["erreur"] = "echec", motif
        out["expires"] += 1
        log(f"travailleur {w['id']} : {motif}")
    elif w["etat"] in ("demande", "cree", "demarre") and age_min > duree_max:
        motif = f"durée de vie de {duree_max} min dépassée : destruction forcée"
        with traces.span("travailleur.expiration", parent=tp, **attrs):
            traces.erreur(motif)
            diag = scw.diagnose(w["zone"], w["scw_server_id"]) if scw is not None and w["scw_server_id"] else None
            conn.execute("UPDATE travailleurs SET etat = 'echec', fini_le = now(), erreur = %s, diagnostic = %s "
                         "WHERE id = %s", (motif, json.dumps(diag) if diag else None, w["id"]))
        w["etat"] = "echec"
        out["expires"] += 1
        log(f"travailleur {w['id']} : durée de vie dépassée, destruction forcée")
    if w["etat"] in ("termine", "echec"):
        with traces.span("travailleur.destruction", parent=tp, **attrs) as sp:
            gone = _detruire(conn, scw, log, w)
            sp.set_attribute("travailleur.detruit", bool(gone))
        if gone:
            conn.execute("UPDATE travailleurs SET detruit_le = now() WHERE id = %s", (w["id"],))
            w["detruit_le"] = now
            _journal(conn, w, rules)
            out["detruits"] += 1


# Étapes du script de démarrage dont l'échec relève de la machine (Docker, pilote, image, réseau) : l'analyse n'y est
# pour rien et repart en attente ; un échec de l'étape « analyse » (code de sortie du script d'analyse) est le sien
ETAPES_MACHINE = {"machine", "demarrage", "interface_privee", "contact_serveur", "installation_docker",
                  "telechargement_image", "controle_gpu", "analyse_docker"}


def _arretee(scw, w: dict) -> bool:
    """Instance d'un travailleur actif arrêtée d'elle même (« stopped in place » ou « stopped ») : le script de démarrage
    a fini en échec sans pouvoir le dire. Lecture sans span (sondage de chaque minute)."""
    try:
        with traces.sans_instrumentation():
            s = scw.server(w["zone"], w["scw_server_id"])
    except Exception:
        return False
    return bool(s) and s.get("state") in ("stopped in place", "stopped")


def _echec_signale(conn, w: dict, tp, attrs: dict, etape: str, motif: str):
    """Échec signalé par le travailleur (ou instance arrêtée) : span « travailleur.echec » qui ferme la trace, analyse
    remise en attente si la machine est en cause, en échec sinon ; une seule fois."""
    machine = etape in ETAPES_MACHINE
    with traces.span("travailleur.echec", parent=tp, **{**attrs, "travailleur.etape": etape,
                                                        "travailleur.cause": "machine" if machine else "analyse"}):
        traces.erreur(motif)
        if machine:
            remettre_en_attente(conn, w, f"travailleur {w['id']} en échec ({etape}) : {motif}"[:900])
        else:
            n = conn.execute("UPDATE analyses SET status = 'failed', completed_at = now(), error = %s "
                             "WHERE travailleur_id = %s AND status IN ('pending', 'running')",
                             (f"travailleur : {motif}"[:1000], w["id"])).rowcount
            traces.attributs(**{"travailleur.analyses_en_echec": n})
        conn.execute("UPDATE travailleurs SET mesures = coalesce(mesures, '{}'::jsonb) || '{\"echec_traite\": true}' "
                     "WHERE id = %s", (w["id"],))


def remettre_en_attente(conn, w: dict, motif: str) -> int:
    """Analyses du travailleur remises en attente d'un calcul, reprises au cycle suivant avec le type suivant : pas
    d'échec, pas de nouvelle analyse (le plafond de scènes n'est pas entamé une seconde fois)."""
    n = conn.execute(
        "UPDATE analyses SET status = 'attente_gpu', attente_depuis = coalesce(attente_depuis, requested_at), "
        "prochain_essai = now(), calcul_motif = %s WHERE travailleur_id = %s AND status IN ('pending', 'running')",
        (motif[:1000], w["id"])).rowcount
    traces.attributs(**{"travailleur.analyses_en_attente": n})
    return n


def incompatibles(conn) -> dict:
    """Types marqués incompatibles et encore actifs : {(type, zone): fin du marquage}."""
    return {(t, z): fin for t, z, fin in conn.execute(
        "SELECT commercial_type, zone, max(jusqu_au) FROM types_incompatibles WHERE leve_le IS NULL AND jusqu_au > now() "
        "GROUP BY 1, 2").fetchall()}


def marquer_incompatible(conn, rules: dict, w: dict, log=print) -> bool:
    """Type et zone d'un travailleur au GPU inutilisable marqués incompatibles pendant `incompatibilite_jours` (7) :
    jamais essayés d'ici là (sauf levée à la main). Un seul marquage actif par type et zone ; la règle de Grafana
    envoie une alerte par marquage."""
    jours = rules["travailleurs"].get("incompatibilite_jours", 7)
    with traces.span("travailleur.marquage_incompatible", **{"travailleur.type": w.get("commercial_type"),
                                                             "travailleur.zone": w.get("zone"), "marquage.jours": jours}):
        if (w.get("commercial_type"), w.get("zone")) in incompatibles(conn):
            traces.attributs(**{"marquage.deja_actif": True})
            return False
        conn.execute("INSERT INTO types_incompatibles (commercial_type, zone, jusqu_au, message, travailleur_id, trace_id) "
                     "VALUES (%s, %s, now() + make_interval(days => %s), %s, %s, %s)",
                     (w.get("commercial_type"), w.get("zone"), jours, (w.get("erreur") or "")[:1000], w["id"],
                      traces.trace_de(w.get("traceparent"))))
        log(f"travailleur {w['id']} : {w.get('commercial_type')} en {w.get('zone')} marqué incompatible pour {jours} jours")
        return True


def _resultats(conn, handlers: dict, w: dict) -> bool:
    """Résultats reçus traités (handlers[tache]) ; faux si un autre processus les traite déjà."""
    try:
        # Tout ou rien, et une seule fois : la ligne est verrouillée (un autre processus la saute), relue, et
        # passe à « termine » dans la même transaction que l'écriture des détections
        with conn.transaction():
            if conn.execute("SELECT 1 FROM travailleurs WHERE id = %s AND etat = 'resultats' "
                            "FOR UPDATE SKIP LOCKED", (w["id"],)).fetchone() is None:
                return False
            summary = handlers[w["tache"]](conn, w)
            # Le travailleur peut s'être arrêté de lui même (données inaccessibles, licence…) : ce qu'il a
            # traité est gardé, l'exécution est en échec avec son motif
            motif = failure(w["resultat"] or {})
            conn.execute("UPDATE travailleurs SET etat = %s, erreur = %s, fini_le = now(), resultat = NULL, "
                         "mesures = mesures || %s WHERE id = %s",
                         ("echec" if motif else "termine", motif and f"travailleur : {motif}"[:1000],
                          json.dumps(summary, default=str), w["id"]))
        w["etat"] = "echec" if motif else "termine"
        w["erreur"] = motif and f"travailleur : {motif}"
        w["mesures"] = {**(w["mesures"] or {}), **summary}
        traces.attributs(**{f"resultats.{k}": v for k, v in summary.items() if isinstance(v, (int, float))})
        if motif:
            traces.erreur(motif)
    except Exception as e:
        traces.erreur(f"traitement : {e}")
        conn.execute("UPDATE travailleurs SET etat = 'echec', fini_le = now(), erreur = %s WHERE id = %s",
                     (f"traitement : {e}"[:1000], w["id"]))
        w["etat"], w["erreur"] = "echec", str(e)
    return True


def _detruire(conn, scw, log, w: dict) -> bool:
    """Destruction par la garde de Scaleway.destroy ; vrai quand plus rien n'existe."""
    if not w["scw_server_id"]:
        return True
    if scw is None:                                   # clés absentes : rien n'est marqué détruit
        return False
    try:
        return scw.destroy(w["zone"], w["scw_server_id"], w["volumes_bloc"] or [])
    except RefusDestruction as e:
        # Jamais détruite : la ligne est close, sans rien toucher chez Scaleway
        traces.erreur(f"destruction refusée : {e}")
        log(f"travailleur {w['id']} : DESTRUCTION REFUSÉE, {e}")
        conn.execute("UPDATE travailleurs SET erreur = coalesce(erreur || ' ; ', '') || %s, detruit_le = now() "
                     "WHERE id = %s", (f"destruction refusée : {e}"[:500], w["id"]))
        return False
    except ScalewayError as e:
        # API indisponible ou étape refusée : reprise à la minute suivante, sans bloquer les autres
        traces.erreur(f"destruction à reprendre : {e}")
        log(f"travailleur {w['id']} : destruction à reprendre, {e}")
        return False
