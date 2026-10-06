"""Contenu de l'interface : dédoublonnage des tracés EMODnet communs à deux régions, repérage de la photo."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from contenu import _IMAGE, _unique  # noqa: E402


def test_tracks_shared_by_two_regions_are_listed_once():
    rows = [{"name": "SEA ME WE3", "type": "Câble télécom", "bbox": [-5.0, 48.4, -3.0, 49.0]},
            {"name": "SEA ME WE3", "type": "Câble télécom", "bbox": [-5.001, 48.402, -3.0, 49.0]},   # même tracé, autre région
            {"name": "SEA ME WE3", "type": "Câble télécom", "bbox": [3.0, 42.0, 5.0, 43.0]}]         # autre segment
    out = _unique(rows, lambda r: (r["name"], r["type"], tuple(round(v, 2) for v in r["bbox"])))
    assert len(out) == 2 and out[0] is rows[0]


def test_photo_url_is_found_in_the_public_page():
    page = '<img src="https://static.vesselfinder.net/ship-photo/9289518-314001078-abc/1" alt="">'
    assert _IMAGE.search(page).group(0) == "https://static.vesselfinder.net/ship-photo/9289518-314001078-abc/1"
    assert _IMAGE.search("<html>aucune photo</html>") is None
