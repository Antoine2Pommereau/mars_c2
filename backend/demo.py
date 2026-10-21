"""Mode démonstration (lot P, phase F) : masquage des navires pour une démonstration publique.

Activé par un cookie de session (`mars_demo`, une graine aléatoire, effacé à la fermeture du navigateur) : toute réponse
JSON de l'API est réécrite avant de partir, le flux en direct aussi. Noms, MMSI, OMI et indicatifs des navires sont
remplacés par des pseudonymes stables pendant la session (HMAC de la graine), les liens vers les sources et les photos
sont retirés ; aucune donnée réelle n'atteint le navigateur, ni à l'écran ni dans les réponses visibles dans ses outils
de développement. Les infrastructures, régions et zones gardent leur nom (données publiques).

Repérage d'un navire : un objet qui porte mmsi, vessel_id, imo, callsign, ship_type, risks ou matched_by ; ses champs de
nom sont masqués. Les colonnes du trafic (cols, rows) aussi. Puis les noms et numéros réels vus pendant la session sont
remplacés dans tous les textes (contexte, motif) : un nom qui n'aurait jamais été vu dans un champ structuré resterait
lisible dans un texte libre (limite connue, docs/controle.md)."""
import hashlib
import hmac
import json
import re
import secrets
from collections import OrderedDict

COOKIE = "mars_demo"
INDICES = {"mmsi", "vessel_id", "imo", "callsign", "ship_type", "risks", "matched_by"}
NOMS = {"name", "nom", "navire", "shipname", "vessel_name", "ancien_nom", "nouveau_nom"}
NOMS_TOUJOURS = {"ancien_nom", "nouveau_nom", "shipname", "vessel_name", "navire"}
NUMEROS = {"mmsi": "mmsi", "imo": "imo", "omi": "imo"}
RETIRES = {"url", "photo", "photo_url", "source_url", "lien"}
MOTS = ["ALBATROS", "BOREAL", "CORMORAN", "DAUPHIN", "ESTRAN", "FREGATE", "GOELAND", "HALCYON", "IROISE", "JUSANT",
        "KERMOR", "LAMANEUR", "MISAINE", "NORDET", "ORQUE", "PETREL", "QUILLE", "RORQUAL", "SURNORD", "TRAMONTANE",
        "URSA", "VERGUE", "WIND", "XEBEC", "YOLE", "ZEPHYR"]
_SESSIONS: "OrderedDict[str, dict]" = OrderedDict()       # graine : correspondances réel vers pseudonyme
SESSIONS_MAX, NOMS_MAX = 20, 50_000


def nouvelle_graine() -> str:
    return secrets.token_hex(16)


def graine_valide(g: str | None) -> str | None:
    return g if g and re.fullmatch(r"[0-9a-f]{32}", g) else None


class Masque:
    def __init__(self, graine: str):
        self.cle = graine.encode()
        self.vus = _SESSIONS.setdefault(graine, {})
        _SESSIONS.move_to_end(graine)
        while len(_SESSIONS) > SESSIONS_MAX:
            _SESSIONS.popitem(last=False)

    def _h(self, genre: str, valeur: str) -> int:
        return int.from_bytes(hmac.new(self.cle, f"{genre}:{valeur}".encode(), hashlib.sha256).digest()[:8], "big")

    def _retenir(self, reel: str, pseudo: str):
        if len(reel) >= 3 and len(self.vus) < NOMS_MAX:
            self.vus[reel] = pseudo

    def nom(self, reel: str) -> str:
        r = reel.strip()
        if not r:
            return reel
        h = self._h("nom", r.upper())
        p = f"{MOTS[h % len(MOTS)]} {100 + (h >> 8) % 900}"
        self._retenir(r, p)
        return p

    def mmsi(self, reel) -> int | str:
        """Pays gardé (trois premiers chiffres, le pavillon reste affiché), six chiffres pseudonymes."""
        s = str(reel)
        if not s.isdigit() or len(s) != 9:
            return reel
        p = s[:3] + f"{self._h('mmsi', s) % 1_000_000:06d}"
        self._retenir(s, p)
        return int(p) if isinstance(reel, int) else p

    def imo(self, reel) -> int | str:
        """Numéro OMI pseudonyme à sept chiffres, avec une clé de contrôle valide."""
        s = str(reel)
        if not s.isdigit() or len(s) != 7:
            return reel
        base = f"{9 if s[0] == '9' else 8}{self._h('imo', s) % 100_000:05d}"
        p = base + str(sum(int(c) * w for c, w in zip(base, range(7, 1, -1))) % 10)
        self._retenir(s, p)
        return int(p) if isinstance(reel, int) else p

    # Parcours

    def masquer(self, obj):
        obj = self._structure(obj, False)
        if not self.vus:
            return obj
        motif = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(k) for k in sorted(self.vus, key=len, reverse=True))
                           + r")(?![\w])")
        return self._textes(obj, motif)

    def _numero(self, cle: str, v):
        return self.mmsi(v) if NUMEROS[cle] == "mmsi" else self.imo(v)

    def _structure(self, o, navire: bool):
        if isinstance(o, list):
            return [self._structure(x, navire) for x in o]
        if not isinstance(o, dict):
            return o
        if isinstance(o.get("cols"), list) and isinstance(o.get("rows"), list):      # trafic en colonnes
            cols = o["cols"]
            idx = {i: c for i, c in enumerate(cols) if c in NOMS or c in NUMEROS or c in ("callsign",)}
            rows = [[self._cellule(idx[i], v) if i in idx else self._structure(v, False) for i, v in enumerate(r)]
                    for r in o["rows"]]
            return {**{k: self._structure(v, False) for k, v in o.items() if k not in ("rows",)}, "rows": rows}
        est_navire = navire or bool(INDICES & o.keys())
        out = {}
        for k, v in o.items():
            if k in RETIRES and (est_navire or k != "url"):
                out[k] = None
            elif k in RETIRES and k == "url" and isinstance(v, str) and "opensanctions" in v:
                out[k] = None
            elif isinstance(v, str) and ((est_navire and k in NOMS) or k in NOMS_TOUJOURS):
                out[k] = self.nom(v)
            elif k == "callsign" and isinstance(v, str):
                out[k] = self.nom(v).split(" ")[0][:3] + v[-2:] if len(v) > 2 else v
            elif k in NUMEROS and isinstance(v, (int, str)) and (est_navire or k in ("mmsi", "imo")):
                out[k] = self._numero(k, v)
            elif k in NUMEROS and isinstance(v, list):
                out[k] = [self._numero(k, x) if isinstance(x, (int, str)) else x for x in v]
            elif k in ("navire", "navires", "navire_liste", "identites", "entries", "candidats_ais", "watch"):
                out[k] = self._structure(v, True)
            else:
                out[k] = self._structure(v, False)
        return out

    def _cellule(self, col: str, v):
        if v is None:
            return v
        if col in NOMS and isinstance(v, str):
            return self.nom(v)
        if col in NUMEROS:
            return self._numero(col, v)
        if col == "callsign" and isinstance(v, str):
            return self.nom(v).split(" ")[0][:3]
        return v

    def _textes(self, o, motif):
        if isinstance(o, str):
            return motif.sub(lambda m: self.vus[m.group(0)], o) if len(o) >= 3 else o
        if isinstance(o, list):
            return [self._textes(x, motif) for x in o]
        if isinstance(o, dict):
            return {k: self._textes(v, motif) for k, v in o.items()}
        return o


def masquer_json(graine: str, corps: bytes) -> bytes:
    return json.dumps(Masque(graine).masquer(json.loads(corps)), ensure_ascii=False, default=str).encode()
