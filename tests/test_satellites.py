"""Calendrier des passages Sentinel 1 et 2 : lecture du catalogue, des pages et des fichiers de plans de l'ESA."""
from datetime import datetime, timezone

from mars.satellites import current_plans, group_catalogue, parse_plan, pass_key, plan_links, satellite_code

NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)
SQUARE = {"type": "Polygon", "coordinates": [[[-5, 48], [-4, 48], [-4, 49], [-5, 49], [-5, 48]]]}


def product(platform, start, end, orbit, datatake=None, mode="IW", state="ascending"):
    return {"id": f"{platform}{start}", "geometry": SQUARE, "properties": {
        "platform": platform, "start_datetime": start, "end_datetime": end, "datetime": start,
        "sat:absolute_orbit": orbit, "sat:relative_orbit": 59, "sat:orbit_state": state,
        "sar:instrument_mode": mode, "eopf:datatake_id": datatake}}


def test_codes_and_keys():
    assert satellite_code("sentinel-1d") == "S1D" and satellite_code("sentinel-2c") == "S2C"
    assert pass_key("S1C", 9782) == "S1C_OR9782"


def test_catalogue_products_of_one_orbit_form_one_pass():
    """Tranches Sentinel 1 d'une même orbite (même deux prises de vue) et tuiles Sentinel 2 : un passage chacune."""
    s1 = group_catalogue([
        product("sentinel-1c", "2026-10-07T17:37:35Z", "2026-10-07T17:38:00Z", 9782, "79865"),
        product("sentinel-1c", "2026-10-07T17:38:00Z", "2026-10-07T17:38:25Z", 9782, "79865"),
        product("sentinel-1c", "2026-10-07T17:40:30Z", "2026-10-07T17:40:55Z", 9782, "79866"),
        product("sentinel-1d", "2026-10-07T06:16:40Z", "2026-10-07T06:17:05Z", 4913, "37906", state="descending"),
    ], "S1")
    by = {p["key"]: p for p in s1}
    assert set(by) == {"S1C_OR9782", "S1D_OR4913"}
    p = by["S1C_OR9782"]
    assert p["produits"] == 3 and len(p["geoms"]) == 3 and p["statut"] == "acquis"
    assert p["start"].isoformat() == "2026-10-07T17:37:35+00:00" and p["end"].isoformat() == "2026-10-07T17:40:55+00:00"
    s2 = group_catalogue([product("sentinel-2a", "2026-10-07T11:21:31Z", "2026-10-07T11:21:31Z", 50100, mode=None),
                          product("sentinel-2a", "2026-10-07T11:21:35Z", "2026-10-07T11:21:35Z", 50100, mode=None)], "S2")
    assert len(s2) == 1 and s2[0]["mode"] == "MSI" and s2[0]["produits"] == 2


def test_plan_links_keep_latest_current_file_per_satellite():
    html = ('<a href="/documents/d/sentinel/s1d_mp_user_20260930t182608_20261020t210500">'
            '<a href="/documents/d/sentinel/s1d_mp_user_20261007t190903_20261027t210500">'
            '<a href="/documents/d/sentinel/s1d_mp_user_20261009t190903_20261029t210500">'     # pas encore en vigueur
            '<a href="/documents/d/sentinel/s1c_mp_user_20260901t000000_20260921t000000">')    # périmé
    plans = current_plans(plan_links(html, "S1"), NOW)
    assert plans == [("S1D", "https://sentinels.copernicus.eu/documents/d/sentinel/s1d_mp_user_20261007t190903_20261027t210500")]
    s2 = plan_links('<a href="https://sentinels.copernicus.eu/documents/d/sentinel/s2c_mp_acq__kml_20261001t113000_20261019t143000">', "S2")
    assert list(s2) == ["S2C"]


def placemark(t0, t1, coords, sat="S1D", dt="9412", orbit="4913", mode="IW"):
    data = "".join(f'<Data name="{k}"><value>{v}</value></Data>' for k, v in [
        ("SatelliteId", sat), ("DatatakeId", dt), ("Mode", mode), ("ObservationTimeStart", t0),
        ("ObservationTimeStop", t1), ("OrbitAbsolute", orbit), ("OrbitRelative", "147")] if v is not None)
    return (f"<Placemark><name>{t0}</name><ExtendedData>{data}</ExtendedData><LinearRing><coordinates>{coords}"
            "</coordinates></LinearRing></Placemark>")


def kml(*placemarks):
    return ('<?xml version="1.0" encoding="iso-8859-1"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            + "".join(placemarks) + "</Document></kml>").encode("latin-1")


def test_plan_keeps_future_passes_over_our_regions():
    ouessant = "-6,48,0 -4,48,0 -4,49,0 -6,49,0 -6,48,0"
    data = kml(
        placemark("2026-10-08T06:16:40", "2026-10-08T06:18:00", ouessant),                       # retenu
        placemark("2026-10-08T06:20:00", "2026-10-08T06:21:00", ouessant, dt="9413"),            # même orbite
        placemark("2026-10-07T10:00:00", "2026-10-07T10:02:00", ouessant, orbit="4900"),         # déjà passé
        placemark("2026-10-08T07:00:00", "2026-10-08T07:02:00", "150,-20,0 155,-20,0 155,-25,0 150,-20,0", orbit="4914"),
        placemark("2026-10-08T08:00:00", "2026-10-08T08:02:00", "179,40,0 -179,40,0 -179,45,0 179,40,0", orbit="4915"),
    )
    (p,) = parse_plan(data, "S1D", "S1", NOW)
    assert p["key"] == "S1D_OR4913" and p["statut"] == "prevu" and p["datatake"] == str(0x9412)
    assert p["orbit_direction"] == "descending" and len(p["geoms"]) == 2
    assert p["end"].isoformat() == "2026-10-08T06:21:00+00:00"


def test_s2_plan_has_no_satellite_field():
    data = kml(placemark("2026-10-08T10:39:00.100", "2026-10-08T10:40:16.0", "-6,47,0 -3,47,0 -3,50,0 -6,47,0",
                         sat=None, dt=None, orbit="10903", mode="NOBS"))
    (p,) = parse_plan(data, "S2B", "S2", NOW)
    assert p["satellite"] == "S2B" and p["key"] == "S2B_OR10903" and p["datatake"] is None


def test_s1_planned_time_is_interpolated_over_our_regions():
    """Prise de vue ascendante de 40° à 60° de latitude en 10 minutes, entre 5° ouest et le méridien : Gascogne,
    Bretagne et Manche (43,3° à 51,2°) sont survolées de la 99e seconde à la 336e (à vitesse constante en latitude)."""
    t0, t1 = datetime(2026, 10, 8, 17, 30, tzinfo=timezone.utc), datetime(2026, 10, 8, 17, 40, tzinfo=timezone.utc)
    from mars.satellites import over_regions
    a, b = over_regions(t0, t1, 40, 60, True, (-5, 40, 0, 60))
    assert round((a - t0).total_seconds()) == 99 and round((b - t0).total_seconds()) == 336
    a, b = over_regions(t0, t1, 40, 60, False, (-5, 40, 0, 60))            # descendante : dans l'autre sens
    assert round((a - t0).total_seconds()) == 264 and round((b - t0).total_seconds()) == 501
    assert over_regions(t0, t1, 40, 60, True, (20, 40, 25, 60)) is None   # bande à l'est de nos régions
