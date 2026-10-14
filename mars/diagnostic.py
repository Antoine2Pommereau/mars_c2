"""Diagnostic Sentinel 1 avant tout essai réel (`taches.py sentinel-diagnostic`), en lecture seule.

Aucune création, aucun coût : uniquement des lectures (GET) sur les API Scaleway et une lecture de 16 octets d'une image
Copernicus. Chaque vérification rend une ligne : PRÊT, MANQUE (à corriger avant d'essayer) ou À VÉRIFIER (illisible
avec la clé du serveur : à contrôler dans la console Scaleway). L'ordre suit celui du lancement : Copernicus d'abord,
Scaleway ensuite.
"""
import json
import os
from datetime import datetime, timedelta, timezone

PRET, MANQUE, A_VERIFIER, INFO = "PRÊT", "MANQUE", "À VÉRIFIER", "INFO"      # INFO : hors du verdict
DROITS = ("InstancesFullAccess", "PrivateNetworksFullAccess", "BlockStorageFullAccess")


def _get(scw, path: str):
    """Lecture seule sur l'API Scaleway : (code HTTP, corps JSON ou texte)."""
    from mars.travailleurs import API
    r = scw.s.request("GET", f"{API}{path}", timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = r.text[:300]
    return r.status_code, body


def copernicus(session, now: datetime) -> list[tuple[str, str, str]]:
    """Deux paires de clés S3 : celle du serveur (ne le quitte jamais) et celle dédiée aux travailleurs (remise par le
    réseau privé avec la tâche). Pour chacune : présence, date d'expiration (reportée dans le .env), puis lecture réelle
    de 16 octets de l'image VV d'une scène récente, signature par en tête (Copernicus refuse les adresses présignées,
    14/10/2026)."""
    from mars import sentinel
    out, pairs = [], []
    for pair, required in ((sentinel.SERVEUR, False), (sentinel.TRAVAILLEUR, True)):
        missing = [pair[k] for k in ("cle", "secret") if not os.environ.get(pair[k])]
        if missing:
            out.append((MANQUE, f"Clés Copernicus {pair['nom']}", f"absentes du .env : {', '.join(missing)}"))
            continue
        out.append((PRET, f"Clés Copernicus {pair['nom']}", "présentes dans le .env"))
        out.append(_expiry(pair, now, required))
        pairs.append(pair)
    if not pairs:
        return out
    try:
        r = session.post(sentinel.STAC, headers=sentinel.HEADERS, timeout=60, json={
            "collections": ["sentinel-1-grd"], "bbox": [-6.8, 47.3, -3.0, 49.6], "limit": 5,
            "datetime": f"{now - timedelta(days=4):%Y-%m-%dT%H:%M:%SZ}/{now:%Y-%m-%dT%H:%M:%SZ}"})
        r.raise_for_status()
        scenes = [s for s in (sentinel.scene_from_item(f) for f in r.json().get("features", [])) if s]
    except Exception as e:
        return out + [(MANQUE, "Catalogue Copernicus", f"injoignable : {type(e).__name__} {str(e)[:160]}")]
    for pair in pairs:
        rubrique = f"Clés Copernicus {pair['nom']} valides"
        if not scenes:
            out.append((A_VERIFIER, rubrique, "aucune scène IW sur la Bretagne depuis 4 jours pour l'essai"))
            continue
        motif = sentinel.check_cdse(session, scenes[0]["vv"], pair)
        out.append((MANQUE, rubrique, motif) if motif else
                   (PRET, rubrique, f"16 octets lus de {scenes[0]['id']} (image VV, signature par en tête)"))
    return out


def _expiry(pair: dict, now: datetime, required: bool = False) -> tuple[str, str, str]:
    """Date d'expiration d'une paire de clés S3 de Copernicus. Elle n'est pas lisible avec les seules clés (le
    gestionnaire de clés demande une connexion au compte) : on la reporte dans le .env à la création (AAAA-MM-JJ).
    `required` : pour la paire des travailleurs, dont la surveillance est demandée, une date absente est à vérifier."""
    rubrique, var = f"Expiration des clés {pair['nom']}", pair["expire"]
    value = os.environ.get(var)
    if not value:
        return (A_VERIFIER if required else INFO, rubrique,
                f"non renseignée : {var}=AAAA-MM-JJ dans le .env, date choisie sur "
                "https://eodata-s3keysmanager.dataspace.copernicus.eu")
    try:
        day = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return (A_VERIFIER, rubrique, f"{var} illisible ({value!r}) : format AAAA-MM-JJ attendu")
    left = (day - now.date()).days
    shown = day.strftime("%d/%m/%Y")
    if left < 0:
        return (MANQUE, rubrique, f"clés expirées le {shown} : les régénérer")
    if left < 14:
        return (A_VERIFIER, rubrique, f"clés valables jusqu'au {shown}, encore {left} jour(s) : penser à les régénérer")
    return (PRET, rubrique, f"clés valables jusqu'au {shown} ({left} jours)")


def scaleway(scw, rules: dict, show=print) -> list[tuple[str, str, str]]:
    """Clés, zone du GPU, image GPU résolue (fiche brute affichée), disponibilité et quota du L4, droits, réseau privé."""
    from mars.travailleurs import ScalewayError, type_params
    if scw is None:
        return [(MANQUE, "Clés Scaleway", "SCW_SECRET_KEY ou SCW_PROJECT_ID absentes du .env")]
    t = type_params(rules, "sentinel")
    zone, ctype = t.get("zone", rules["travailleurs"]["zone"]), t["commercial_type"]
    region = zone.rsplit("-", 1)[0]
    out = [(PRET, "Clés Scaleway", f"présentes, projet {scw.project}")]
    for k in ("SCW_PRIVATE_NETWORK_ID", "MARS_IP_PRIVEE"):
        out.append((PRET, k, os.environ[k]) if os.environ.get(k) else (MANQUE, k, "absente du .env"))

    code, body = _get(scw, f"/instance/v1/zones/{zone}/servers?project={scw.project}&per_page=1")
    out.append((PRET, f"API Instances en {zone}", "lecture autorisée") if code == 200 else
               (MANQUE, f"API Instances en {zone}", f"HTTP {code} : {str(body)[:200]}"))

    # Image GPU : résolution complète (catalogue, fiche de l'API Instances, instantané bloc), fiche brute affichée
    try:
        img = scw.resolve_image(zone, t["image_label"], ctype, t.get("arch", "x86_64"), t.get("volume", "l_ssd"))
        out.append((PRET, "Image GPU", f"{img['name']} ({img['id']}), volume racine de {img['root_size'] / 1e9:.0f} Go, "
                                       f"démarrage sur {img['root_type']}"))
        show(f"Fiche brute de l'image {img['id']} (API Instances, {zone}) :")
        show(json.dumps(img["fiche"], ensure_ascii=False, indent=2, default=str))
    except ScalewayError as e:
        out.append((MANQUE, "Image GPU", str(e)[:400]))
        code, cat = _get(scw, f"/marketplace/v2/local-images?image_label={t['image_label']}&zone={zone}")
        show(f"Catalogue brut pour {t['image_label']} en {zone} (HTTP {code}) :")
        show(json.dumps(cat, ensure_ascii=False, indent=2, default=str)[:4000])
        for im in (cat.get("local_images", []) if isinstance(cat, dict) else []):
            if im.get("type") == ("instance_sbs" if t.get("volume") == "sbs_volume" else "instance_local"):
                c2, fiche = _get(scw, f"/instance/v1/zones/{zone}/images/{im['id']}")
                show(f"Fiche brute de l'image {im['id']} (API Instances, HTTP {c2}) :")
                show(json.dumps(fiche, ensure_ascii=False, indent=2, default=str)[:4000])

    out.append(_availability(scw, zone, ctype, show))

    out += _quota(scw, ctype, zone)
    out += _rights(scw, zone)

    pn = os.environ.get("SCW_PRIVATE_NETWORK_ID")
    if pn:
        code, body = _get(scw, f"/vpc/v2/regions/{region}/private-networks/{pn}")
        if code == 200 and isinstance(body, dict):
            out.append((PRET if body.get("region") == region else MANQUE, f"Réseau privé en {zone}",
                        f"« {body.get('name')} », région {body.get('region')} : régional, donc rattachable depuis {zone}"))
        else:
            out.append((MANQUE, f"Réseau privé en {zone}", f"HTTP {code} : {str(body)[:200]}"))
    return out


def _availability(scw, zone: str, ctype: str, show) -> tuple[str, str, str]:
    """Disponibilité annoncée du type d'instance. La réponse est paginée (100 types au plus par page, GPU au delà de la
    première : « illisible (HTTP 200) » au premier diagnostic du 13/10/2026) : pages lues jusqu'à trouver le type ;
    introuvable, la réponse brute de la première page est affichée."""
    rubrique, first, seen = f"{ctype} en {zone}", None, 0
    for page in range(1, 11):
        code, body = _get(scw, f"/instance/v1/zones/{zone}/products/servers/availability?per_page=100&page={page}")
        if code != 200 or not isinstance(body, dict):
            show(f"Réponse brute de la disponibilité (page {page}, HTTP {code}) :")
            show(json.dumps(body, ensure_ascii=False, indent=2, default=str)[:3000])
            return (A_VERIFIER, rubrique, f"disponibilité illisible (HTTP {code}, réponse brute affichée)")
        first = first or body
        servers = body.get("servers") or {}
        seen += len(servers)
        av = (servers.get(ctype) or {}).get("availability")
        if av:
            return (PRET if av in ("available", "scarce") else A_VERIFIER, rubrique,
                    f"disponibilité annoncée : {av}" + ("" if av == "available" else " (stock faible ou épuisé)"))
        if not servers or seen >= int(body.get("total_count") or 0):
            break
    show(f"Réponse brute de la disponibilité (première page, {seen} types lus en tout) :")
    show(json.dumps(first, ensure_ascii=False, indent=2, default=str)[:3000])
    return (A_VERIFIER, rubrique, f"{ctype} absent des {seen} types listés (réponse brute affichée)")


def _quota(scw, ctype: str, zone: str) -> list[tuple[str, str, str]]:
    """Quota du type GPU (API IAM, organisation du projet). Une clé limitée au projet n'a en général pas le droit de
    le lire : la ligne le dit, sans conclure."""
    code, proj = _get(scw, f"/account/v3/projects/{scw.project}")
    org = proj.get("organization_id") if code == 200 and isinstance(proj, dict) else None
    if not org:
        return [(A_VERIFIER, f"Quota {ctype}", f"organisation illisible avec cette clé (HTTP {code}) : console Scaleway, "
                                                "Organization, Quotas (L4-1-24G : 1 après validation de l'identité)")]
    code, body = _get(scw, f"/iam/v1alpha1/quota?organization_id={org}&page_size=100")
    if code != 200 or not isinstance(body, dict):
        return [(A_VERIFIER, f"Quota {ctype}", f"quotas illisibles avec cette clé (HTTP {code}) : console Scaleway, "
                                                "Organization, Quotas")]
    key = ctype.lower().replace("-", "_")
    found = [q for q in body.get("quota", []) if key in q.get("name", "").lower() or "l4" in q.get("name", "").lower()]
    if not found:
        return [(A_VERIFIER, f"Quota {ctype}", "aucun quota à ce nom dans la liste de l'organisation")]
    lines = []
    for q in found:
        limits = q.get("limits") or [{"limit": q.get("limit"), "unlimited": q.get("unlimited")}]
        mine = [x for x in limits if x.get("zone") in (None, zone)]
        val = [("illimité" if x.get("unlimited") else x.get("limit")) for x in mine]
        ok = any(x.get("unlimited") or (x.get("limit") or 0) >= 1 for x in mine)
        lines.append((PRET if ok else MANQUE, f"Quota {q.get('name')}",
                      f"{val} en {zone}" + ("" if ok else " : demander la validation de l'identité ou le support")))
    return lines


def _rights(scw, zone: str) -> list[tuple[str, str, str]]:
    """Droits de la clé : lecture du stockage bloc (preuve directe), puis ensembles de droits de sa politique (API IAM,
    lisible seulement avec un droit de lecture IAM)."""
    code, body = _get(scw, f"/block/v1alpha1/zones/{zone}/volumes?project_id={scw.project}&page_size=1")
    out = [(PRET, "Stockage bloc", "lecture autorisée (suppression des volumes : droit BlockStorageFullAccess)")
           if code == 200 else (MANQUE, "Stockage bloc", f"HTTP {code} : ajouter BlockStorageFullAccess à la politique")]
    access = os.environ.get("SCW_ACCESS_KEY")
    if not access:
        return out + [(A_VERIFIER, "Droits de la clé", "SCW_ACCESS_KEY absente du .env : politique non lue")]
    code, key = _get(scw, f"/iam/v1alpha1/api-keys/{access}")
    app = key.get("application_id") if code == 200 and isinstance(key, dict) else None
    if not app:
        return out + [(A_VERIFIER, "Droits de la clé", f"politique illisible avec cette clé (HTTP {code}) : console, IAM, "
                                                       f"Applications, politique : {', '.join(DROITS)}")]
    sets: set[str] = set()
    code, pols = _get(scw, f"/iam/v1alpha1/policies?application_id={app}&page_size=50")
    for pol in (pols.get("policies", []) if code == 200 and isinstance(pols, dict) else []):
        c2, rules = _get(scw, f"/iam/v1alpha1/rules?policy_id={pol['id']}&page_size=50")
        for r in (rules.get("rules", []) if c2 == 200 and isinstance(rules, dict) else []):
            sets.update(r.get("permission_set_names") or [])
    if not sets:
        return out + [(A_VERIFIER, "Droits de la clé", "ensembles de droits illisibles : vérifier dans la console")]
    missing = [d for d in DROITS if d not in sets]
    return out + [(MANQUE, "Droits de la clé", f"manquent : {', '.join(missing)}") if missing else
                  (PRET, "Droits de la clé", ", ".join(DROITS))]


def block_volumes(scw, rules: dict, recorded: list | None = None) -> list[tuple[str, str, str]]:
    """Volumes bloc par l'API Block Storage. L'API Instances donne une taille 0 pour un volume « sbs_volume » (premier
    essai réel du 14/10/2026) : la vérification du lancement lit la taille ici. `recorded` : [(travailleur, [volumes])]
    enregistrés en base pour les travailleurs Sentinel récents ; chacun doit être supprimé (404 à la relecture), sinon il
    est facturé."""
    from mars.travailleurs import type_params
    if scw is None:
        return []
    t = type_params(rules, "sentinel")
    zone = t.get("zone", rules["travailleurs"]["zone"])
    out, rubrique = [], "Lecture d'un volume bloc"
    code, body = _get(scw, f"/block/v1alpha1/zones/{zone}/volumes?project_id={scw.project}&page_size=1")
    vols = body.get("volumes", []) if code == 200 and isinstance(body, dict) else []
    if vols:
        c2, v = _get(scw, f"/block/v1alpha1/zones/{zone}/volumes/{vols[0]['id']}")
        size = int(v.get("size") or 0) if c2 == 200 and isinstance(v, dict) else 0
        out.append((PRET, rubrique, f"volume {vols[0]['id']} : {size / 1e9:.0f} Go lus par l'API Block Storage") if size
                   else (A_VERIFIER, rubrique, f"taille illisible (HTTP {c2}) : le lancement ne refusera pas sur la "
                                               "taille seule"))
    elif code == 200:
        out.append((PRET, rubrique, f"aucun volume bloc en {zone} ; route de lecture autorisée (taille lue au lancement)"))
    for wid, ids in recorded or []:
        for vid in ids or []:
            c2, v = _get(scw, f"/block/v1alpha1/zones/{zone}/volumes/{vid}")
            r = f"Volume bloc du travailleur {wid}"
            if c2 == 404:
                out.append((PRET, r, f"{vid} supprimé (absent de l'API Block Storage)"))
            elif c2 == 200 and isinstance(v, dict):
                out.append((MANQUE, r, f"{vid} encore présent ({int(v.get('size') or 0) / 1e9:.0f} Go, état "
                                       f"{v.get('status')}) : facturé, à supprimer dans la console"))
            else:
                out.append((A_VERIFIER, r, f"{vid} illisible (HTTP {c2})"))
    return out


def config(rules: dict) -> list[tuple[str, str, str]]:
    s = rules["sentinel"]
    if s["scenes_max_par_jour"] <= 0:
        return [(INFO, "Plafond de scènes", "scenes_max_par_jour vaut 0 : Sentinel arrêté, aucune analyse ne sera lancée "
                                            "(le relever une fois tout au vert, docs/sentinel.md)")]
    return [(INFO, "Plafond de scènes", f"{s['scenes_max_par_jour']} par jour, budget {s['budget_mensuel_eur']} €")]


def run(rules: dict, session, scw, now: datetime | None = None, show=print,
        recorded: list | None = None) -> list[tuple[str, str, str]]:
    now = now or datetime.now(timezone.utc)
    lines = copernicus(session, now)
    lines += scaleway(scw, rules, show)
    lines += block_volumes(scw, rules, recorded)
    lines += config(rules)
    return lines


def report(lines: list[tuple[str, str, str]]) -> str:
    width = max(len(r) for _s, r, _d in lines)
    txt = [f"{s:<10} {r:<{width}}  {d}" for s, r, d in lines]
    n = {k: sum(1 for s, _r, _d in lines if s == k) for k in (PRET, MANQUE, A_VERIFIER)}
    verdict = ("Tout est prêt pour le premier essai réel." if not n[MANQUE] and not n[A_VERIFIER] else
               "À corriger avant tout essai." if n[MANQUE] else "Rien ne manque ; contrôler les points « À VÉRIFIER ».")
    return "\n".join(txt + ["", f"{n[PRET]} prêts, {n[MANQUE]} manquants, {n[A_VERIFIER]} à vérifier. {verdict}"])
