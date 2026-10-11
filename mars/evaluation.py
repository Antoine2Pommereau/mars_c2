"""Une détection satellite sans navire AIS apparié est elle évaluable ? Commun aux détections VIIRS et, au lot C, aux
détections Sentinel : une absence d'AIS ne prouve rien là où notre AIS ne voit pas.

Ordre des critères : coupure du flux AIS à l'heure du passage ; puis, dès que la zone de réception fiable est
construite (table reception_cells non vide), elle seule décide (hors zone : non évaluable) ; avant qu'elle n'existe,
critère provisoire : au moins un navire AIS reçu dans un rayon autour de la détection, à l'heure du passage."""

CRITERE_ZONE, CRITERE_RAYON = "zone_de_reception_fiable", "navires_dans_le_rayon"


def critere(zone_construite: bool) -> str:
    return CRITERE_ZONE if zone_construite else CRITERE_RAYON


def motif_non_evaluable(flux_coupe: bool, zone_construite: bool, dans_zone: bool, navires_autour: int | None,
                        regle: dict) -> str | None:
    """Motif pour lequel la détection n'est pas évaluable, ou None si elle l'est. `regle` : rayon, fenêtre et nombre
    de navires du critère provisoire (couverture_rayon_km, couverture_fenetre_min, couverture_min_navires)."""
    if flux_coupe:
        return "coupure du flux AIS à l'heure du passage"
    if zone_construite:
        return None if dans_zone else "hors de la zone de réception AIS fiable"
    if (navires_autour or 0) < regle["couverture_min_navires"]:
        return (f"aucun navire AIS reçu à moins de {regle['couverture_rayon_km']} km dans les "
                f"{regle['couverture_fenetre_min']} minutes autour du passage : réception AIS absente")
    return None
