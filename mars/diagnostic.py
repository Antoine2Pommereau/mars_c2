"""Diagnostic Sentinel 1 avant tout essai réel (`taches.py sentinel-diagnostic`), en lecture seule, sauf une écriture :
le groupe de sécurité des travailleurs, créé dans chaque zone où il manque (lot Z1).

Aucune instance, aucun coût : sinon des lectures (GET) sur les API Scaleway et une lecture de 16 octets d'une image
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
    from mars.echeances import lire_date
    day, _illisible = lire_date(value)                 # même lecture que la tâche echeances (lot Z1)
    if day is None:
        return (A_VERIFIER, rubrique, f"{var} illisible ({value!r}) : format JJ/MM/AAAA ou AAAA-MM-JJ attendu")
    left = (day - now.date()).days
    shown = day.strftime("%d/%m/%Y")
    if left < 0:
        return (MANQUE, rubrique, f"clés expirées le {shown} : les régénérer")
    if left < 14:
        return (A_VERIFIER, rubrique, f"clés valables jusqu'au {shown}, encore {left} jour(s) : penser à les régénérer")
    return (PRET, rubrique, f"clés valables jusqu'au {shown} ({left} jours)")


def scaleway(scw, rules: dict, show=print) -> list[tuple[str, str, str]]:
    """Clés, zone du type de base de Sentinel 1, image résolue (fiche brute affichée), disponibilité des types de la
    liste, droits, groupes de sécurité, réseau privé."""
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

    # Image du type de base : résolution complète (catalogue, fiche de l'API Instances), fiche brute affichée
    try:
        img = scw.resolve_image(zone, t["image_label"], ctype, t.get("arch", "x86_64"), t.get("volume", "l_ssd"))
        out.append((PRET, "Image Sentinel 1", f"{img['name']} ({img['id']}), volume racine de "
                                              f"{img['root_size'] / 1e9:.0f} Go, démarrage sur {img['root_type']}"))
        show(f"Fiche brute de l'image {img['id']} (API Instances, {zone}) :")
        show(json.dumps(img["fiche"], ensure_ascii=False, indent=2, default=str))
    except ScalewayError as e:
        out.append((MANQUE, "Image Sentinel 1", str(e)[:400]))
        code, cat = _get(scw, f"/marketplace/v2/local-images?image_label={t['image_label']}&zone={zone}")
        show(f"Catalogue brut pour {t['image_label']} en {zone} (HTTP {code}) :")
        show(json.dumps(cat, ensure_ascii=False, indent=2, default=str)[:4000])
        for im in (cat.get("local_images", []) if isinstance(cat, dict) else []):
            if im.get("type") == ("instance_sbs" if t.get("volume") == "sbs_volume" else "instance_local"):
                c2, fiche = _get(scw, f"/instance/v1/zones/{zone}/images/{im['id']}")
                show(f"Fiche brute de l'image {im['id']} (API Instances, HTTP {c2}) :")
                show(json.dumps(fiche, ensure_ascii=False, indent=2, default=str)[:4000])

    out.append(_availability(scw, zone, ctype, show))
    out += types_calcul(scw, rules)

    out += _rights(scw, zone)
    out += groupes_securite(scw, rules)

    pn = os.environ.get("SCW_PRIVATE_NETWORK_ID")
    if pn:
        code, body = _get(scw, f"/vpc/v2/regions/{region}/private-networks/{pn}")
        if code == 200 and isinstance(body, dict):
            out.append((PRET if body.get("region") == region else MANQUE, f"Réseau privé en {zone}",
                        f"« {body.get('name')} », région {body.get('region')} : régional, donc rattachable depuis {zone}"))
        else:
            out.append((MANQUE, f"Réseau privé en {zone}", f"HTTP {code} : {str(body)[:200]}"))
    return out


def zones_travailleurs(rules: dict) -> list[str]:
    """Zones où un travailleur peut être créé : zone par défaut, celle de chaque type et de ses secours."""
    p = rules["travailleurs"]
    zones = {p["zone"]}
    for t in p["types"].values():
        zones.add(t.get("zone", p["zone"]))
        zones |= {x.get("zone", t.get("zone", p["zone"])) for x in (t.get("repli_cpu") or [])}
    return sorted(zones)


def groupes_securite(scw, rules: dict) -> list[tuple[str, str, str]]:
    """Groupe de sécurité des travailleurs (lot Z1) dans chaque zone : vérifié, ou créé s'il manque (entrée refusée,
    sortie libre). Seule écriture du diagnostic, idempotente ; un groupe modifié (entrée acceptée) est signalé."""
    from mars.travailleurs import GROUPE_SECURITE, ScalewayError
    out = []
    for zone in zones_travailleurs(rules):
        try:
            g = scw.groupe_securite(zone)
            out.append((PRET, f"Groupe de sécurité en {zone}", f"{GROUPE_SECURITE} ({g['id']}) "
                        + ("créé : entrée refusée, sortie libre" if g["cree"] else "vérifié : entrée refusée, sortie libre")))
        except ScalewayError as e:
            out.append((MANQUE, f"Groupe de sécurité en {zone}", str(e)[:300]))
    return out


def types_calcul(scw, rules: dict) -> list[tuple[str, str, str]]:
    """Chaque type de la liste de calcul Sentinel 1 (GP1-S, puis ses secours), dans l'ordre de la liste : mode de
    facturation, disponibilité annoncée et prix du catalogue Scaleway, comparé au prix de config/rules.yaml (coût estimé). Une
    rupture de stock n'est pas un manque : l'analyse attend et réessaie (lot C3)."""
    from mars.travailleurs import type_params, variantes
    dispo, cat, out = {}, {}, []
    for k, v in enumerate(variantes(rules, "sentinel"), 1):
        t = type_params(rules, "sentinel", v)
        zone, ctype = t.get("zone", rules["travailleurs"]["zone"]), t["commercial_type"]
        try:
            if zone not in dispo:
                dispo[zone], cat[zone] = scw.availability(zone), scw.products(zone)
        except Exception as e:
            out.append((A_VERIFIER, f"Type {k} : {ctype} en {zone}", f"catalogue illisible ({str(e)[:150]})"))
            continue
        etat = dispo[zone].get(ctype, "absent du catalogue")
        prix = (cat[zone].get(ctype) or {}).get("hourly_price")
        ecart = prix is not None and abs(prix - t["prix_heure"]) > 0.05 * t["prix_heure"]
        mode = "à la minute" if t["facturation"] == "minute" else "à l'heure entamée"
        texte = (f"processeur facturé {mode}, {etat}, "
                 + (f"{prix:.4f} €/h au catalogue" if prix is not None else "prix inconnu").replace(".", ",")
                 + (f", {t['prix_heure']} €/h dans config/rules.yaml : à mettre à jour".replace(".", ",") if ecart else ""))
        statut = PRET if etat == "available" and not ecart else A_VERIFIER
        if etat == "absent du catalogue":
            statut = MANQUE
        out.append((statut, f"Type {k} : {ctype} en {zone}", texte))
    return out


def _availability(scw, zone: str, ctype: str, show) -> tuple[str, str, str]:
    """Disponibilité annoncée du type d'instance. La réponse est paginée (100 types au plus par page ; les GPU, cherchés
    alors, étaient au delà de la première : « illisible (HTTP 200) » au premier diagnostic du 13/10/2026) : pages lues jusqu'à trouver le type ;
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


def _refus(e: Exception) -> str:
    """Motif d'un refus de R2 par boto3 : code et message de sa réponse, sinon le type de l'erreur."""
    err = getattr(e, "response", {}).get("Error", {}) if hasattr(e, "response") else {}
    return (f"{err.get('Code')} : {err.get('Message')}" if err else f"{type(e).__name__} {str(e)[:160]}")[:300]


def sentinel2(rules: dict, session, api_variables, modeles=None, principal=None) -> list[tuple[str, str, str]]:
    """Poids du modèle Sentinel 2 dans le seau des modèles (`taches.py sentinel2-diagnostic`), en lecture seule.

    1. Variables du seau des modèles dans ce conteneur (taches), puis dans le conteneur backend, qui présigne les
       adresses remises aux travailleurs (`api_variables` : présence lue par GET /api/travailleurs/variables, jamais
       les valeurs) ; 2. seau joignable par son jeton ; 3. chaque poids présent à sa taille ; 4. un octet lu par une
       adresse présignée de ce jeton, depuis le serveur ; 5. ancienne copie dans le seau principal (doublon à retirer).
    Le filtre IP du jeton ne peut pas se vérifier depuis le serveur (son IP est toujours admise) : le travailleur lit
    un octet de chaque adresse avant Docker et l'image (travailleurs/demarrage.sh, étape controle_r2)."""
    from mars.r2_signature import MODELES, modeles_manquantes, r2_modeles_get_url
    s2 = rules["sentinel2"]
    out = []
    manque = modeles_manquantes()
    for k in MODELES:
        out.append((MANQUE, f"{k} (taches)", "absente du conteneur taches : .env du serveur, puis recréer le conteneur")
                   if k in manque else (PRET, f"{k} (taches)", "présente"))
    try:
        vus = api_variables()
        for k in MODELES:
            out.append((PRET, f"{k} (backend)", "présente") if vus.get(k) else
                       (MANQUE, f"{k} (backend)", "absente du conteneur backend : aucune adresse des poids ne serait "
                                                  "remise aux travailleurs ; .env du serveur, puis recréer backend"))
    except Exception as e:
        out.append((MANQUE, "Variables du backend", f"API injoignable ou trop ancienne : {type(e).__name__} "
                                                    f"{str(e)[:160]}"))
    if manque:
        return out
    nom = os.environ["R2_MODELES_BUCKET"].strip()
    try:
        if modeles is None:
            from mars.r2 import R2
            modeles = R2(seau="modeles")
        objets = modeles.list("modeles/")
        out.append((PRET, "Seau des modèles", f"{nom} joignable par son jeton ({len(objets)} objet(s) sous modeles/)"))
    except Exception as e:
        return out + [(MANQUE, "Seau des modèles", f"{nom} : {_refus(e)}")]
    complets = []
    for p in s2["poids"]:
        rubrique = f"Poids {p['chemin'].split('/')[1]}"
        try:
            taille = modeles.taille(p["r2"])
        except Exception as e:
            out.append((MANQUE, rubrique, _refus(e)))
            continue
        if taille == p["octets"]:
            complets.append(p)
            out.append((PRET, rubrique, f"{taille / 1e9:.2f} Go dans {nom}".replace(".", ",")))
        else:
            out.append((MANQUE, rubrique, ("absent" if taille is None else f"{taille} octets au lieu de {p['octets']}")
                        + " : taches.py sentinel2-poids"))
    if complets:
        p = complets[0]
        try:
            r = session.get(r2_modeles_get_url(p["r2"], expires=60), headers={"Range": "bytes=0-0"}, timeout=30)
            out.append((PRET, "Adresse présignée des poids", f"1 octet lu depuis le serveur (HTTP {r.status_code})")
                       if r.status_code in (200, 206) else
                       (MANQUE, "Adresse présignée des poids", f"HTTP {r.status_code} : {r.text[:200]}"))
        except Exception as e:
            out.append((MANQUE, "Adresse présignée des poids", f"{type(e).__name__} {str(e)[:160]}"))
    out.append((INFO, "Filtre IP du jeton des modèles", "invérifiable depuis le serveur : il ne doit jamais être filtré "
                                                        "par IP ; le travailleur lit un octet avant Docker"))
    if principal is not None:
        doublons = 0
        for p in s2["poids"]:
            try:
                doublons += principal.taille(p["r2"]) is not None
            except Exception:
                pass
        if doublons:
            out.append((INFO, "Ancienne copie des poids", f"{doublons} objet(s) encore dans le seau principal : "
                                                          "taches.py sentinel2-poids --nettoyer"))
    return out


def report(lines: list[tuple[str, str, str]]) -> str:
    width = max(len(r) for _s, r, _d in lines)
    txt = [f"{s:<10} {r:<{width}}  {d}" for s, r, d in lines]
    n = {k: sum(1 for s, _r, _d in lines if s == k) for k in (PRET, MANQUE, A_VERIFIER)}
    verdict = ("Tout est prêt pour le premier essai réel." if not n[MANQUE] and not n[A_VERIFIER] else
               "À corriger avant tout essai." if n[MANQUE] else "Rien ne manque ; contrôler les points « À VÉRIFIER ».")
    return "\n".join(txt + ["", f"{n[PRET]} prêts, {n[MANQUE]} manquants, {n[A_VERIFIER]} à vérifier. {verdict}"])
