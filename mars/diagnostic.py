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
    """Clés S3 présentes, puis valides : lecture réelle de 16 octets de l'image VV d'une scène récente."""
    from mars import sentinel
    out = []
    missing = [k for k in ("CDSE_ACCESS_KEY", "CDSE_SECRET_KEY") if not os.environ.get(k)]
    if missing:
        return [(MANQUE, "Clés Copernicus", f"absentes du .env : {', '.join(missing)}")]
    out.append((PRET, "Clés Copernicus", "présentes dans le .env"))
    try:
        r = session.post(sentinel.STAC, headers=sentinel.HEADERS, timeout=60, json={
            "collections": ["sentinel-1-grd"], "bbox": [-6.8, 47.3, -3.0, 49.6], "limit": 5,
            "datetime": f"{now - timedelta(days=4):%Y-%m-%dT%H:%M:%SZ}/{now:%Y-%m-%dT%H:%M:%SZ}"})
        r.raise_for_status()
        scenes = [s for s in (sentinel.scene_from_item(f) for f in r.json().get("features", [])) if s]
    except Exception as e:
        return out + [(MANQUE, "Catalogue Copernicus", f"injoignable : {type(e).__name__} {str(e)[:160]}")]
    if not scenes:
        return out + [(A_VERIFIER, "Clés Copernicus valides", "aucune scène IW sur la Bretagne depuis 4 jours pour l'essai")]
    motif = sentinel.check_cdse(session, sentinel.signed_urls(scenes[0], now)["vv"])
    out.append((MANQUE, "Clés Copernicus valides", motif) if motif else
               (PRET, "Clés Copernicus valides", f"16 octets lus de {scenes[0]['id']} (image VV)"))
    return out


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

    code, body = _get(scw, f"/instance/v1/zones/{zone}/products/servers/availability")
    av = (body.get("servers", {}).get(ctype) or {}).get("availability") if code == 200 and isinstance(body, dict) else None
    out.append((PRET if av in ("available", "scarce") else A_VERIFIER, f"{ctype} en {zone}",
                f"disponibilité annoncée : {av or f'illisible (HTTP {code})'}"))

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


def config(rules: dict) -> list[tuple[str, str, str]]:
    s = rules["sentinel"]
    if s["scenes_max_par_jour"] <= 0:
        return [(INFO, "Plafond de scènes", "scenes_max_par_jour vaut 0 : Sentinel arrêté, aucune analyse ne sera lancée "
                                            "(le relever une fois tout au vert, docs/sentinel.md)")]
    return [(INFO, "Plafond de scènes", f"{s['scenes_max_par_jour']} par jour, budget {s['budget_mensuel_eur']} €")]


def run(rules: dict, session, scw, now: datetime | None = None, show=print) -> list[tuple[str, str, str]]:
    now = now or datetime.now(timezone.utc)
    lines = copernicus(session, now)
    lines += scaleway(scw, rules, show)
    lines += config(rules)
    return lines


def report(lines: list[tuple[str, str, str]]) -> str:
    width = max(len(r) for _s, r, _d in lines)
    txt = [f"{s:<10} {r:<{width}}  {d}" for s, r, d in lines]
    n = {k: sum(1 for s, _r, _d in lines if s == k) for k in (PRET, MANQUE, A_VERIFIER)}
    verdict = ("Tout est prêt pour le premier essai réel." if not n[MANQUE] and not n[A_VERIFIER] else
               "À corriger avant tout essai." if n[MANQUE] else "Rien ne manque ; contrôler les points « À VÉRIFIER ».")
    return "\n".join(txt + ["", f"{n[PRET]} prêts, {n[MANQUE]} manquants, {n[A_VERIFIER]} à vérifier. {verdict}"])
