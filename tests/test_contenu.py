"""Contenu de l'interface : dédoublonnage des tracés EMODnet communs à deux régions, repérage de la photo."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import asyncio  # noqa: E402

from contenu import _IMAGE, _unique, fetch_photo  # noqa: E402


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


class _Resp:
    def __init__(self, status, text="", ctype="text/html", content=b""):
        self.status_code, self.text, self.headers, self.content = status, text, {"content-type": ctype}, content


class _Client:
    """Faux client HTTP : la fiche publique puis l'image."""
    def __init__(self, page, image=None):
        self.answers = [page, image]

    async def get(self, _url):
        return self.answers.pop(0)


def test_photo_failure_reasons_are_explicit():
    page = _Resp(200, '<img src="https://static.vesselfinder.net/ship-photo/1-2-a/1">')
    jpeg = _Resp(200, ctype="image/jpeg", content=b"x" * 5000)
    assert asyncio.run(fetch_photo(_Client(page, jpeg), 613411302)) == (jpeg.content, None)
    assert asyncio.run(fetch_photo(_Client(_Resp(403)), 1))[1] == "VesselFinder a répondu 403 pour la fiche du navire"
    assert "pas de photo" in asyncio.run(fetch_photo(_Client(_Resp(200, "<html></html>")), 1))[1]
    tiny = _Resp(200, ctype="image/gif", content=b"x" * 43)
    assert asyncio.run(fetch_photo(_Client(page, tiny), 1))[1] == "image refusée par VesselFinder (200, image/gif, 43 octets)"
