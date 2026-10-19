"""Collecte AIS en direct depuis AISStream, et rapport de couverture.

Deux zones françaises : Bretagne et entrée de la Manche (rail d'Ouessant), Méditerranée française
(golfe du Lion, Marseille, Toulon, Côte d'Azur, Corse). Les messages sont archivés en Parquet, par zone et par
heure, et un rapport mesure ce qui compte pour la suite : volume, navires, cargos et pétroliers, retard du flux,
et continuité des trajectoires (part des intervalles de moins de 3 minutes, comme la zone de réception fiable).

Usage :
    export AISSTREAM_API_KEY=ta_cle
    python scripts/ais_live.py collect            # Ctrl + C pour arrêter
    python scripts/ais_live.py report             # sur tout ce qui a été collecté
"""
import argparse
import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mars.ais.live import SOURCE, ZONES  # noqa: E402  (zones et origine partagées avec l'ingestion en base)
from mars import traces  # noqa: E402
from mars.supervision import SIGNE_COLLECTE, VERSION_COLLECTE, toucher  # noqa: E402

OUT = Path(os.environ.get("AIS_LIVE_DIR", ROOT / "data" / "ais_live"))
URL = "wss://stream.aisstream.io/v0/stream"
POSITION_TYPES = ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"]
STATIC_TYPES = ["ShipStaticData", "StaticDataReport"]
FLUSH_S = int(os.environ.get("AIS_FLUSH_S", 60))
# Messages gardés en mémoire quand l'écriture échoue (disque plein), en attendant la place : environ trois heures
# au rythme du 06/10/2026 (680 messages par minute) ; au delà, les plus anciens sont abandonnés, et le journal le dit
MAX_EN_ATTENTE = int(os.environ.get("AIS_MAX_EN_ATTENTE", 120_000))
ECHEC_ECRITURE = ".echec_ecriture_collecte"      # lu par le contrôle de santé (scripts/sante.py)


def zone_of(lat, lon):
    for name, (a, b, c, d) in ZONES.items():
        if a <= lat <= c and b <= lon <= d:
            return name
    return None


def parse_time(s):
    # Format AISStream : '2026-10-05 12:39:19.104049717 +0000 UTC'
    try:
        return pd.Timestamp(s.split(" +")[0], tz="UTC")
    except Exception:
        return pd.NaT


class Buffer:
    def __init__(self):
        self.pos, self.static = [], []
        self.n, self.started = 0, time.time()
        self.mmsi = {z: set() for z in ZONES}

    def add(self, msg):
        kind = msg.get("MessageType")
        meta = msg.get("MetaData", {})
        body = msg.get("Message", {}).get(kind, {})
        now = datetime.now(timezone.utc)
        lat, lon = meta.get("latitude"), meta.get("longitude")
        if lat is None or lon is None:
            return
        zone = zone_of(lat, lon)
        if zone is None:
            return
        self.n += 1
        self.mmsi[zone].add(meta.get("MMSI"))
        if kind in POSITION_TYPES:
            self.pos.append({
                "zone": zone, "mmsi": meta.get("MMSI"), "ts": meta.get("time_utc"), "received_at": now,
                "lat": lat, "lon": lon, "sog": body.get("Sog"), "cog": body.get("Cog"),
                "heading": body.get("TrueHeading"), "nav_status": body.get("NavigationalStatus"),
                "class_b": kind != "PositionReport", "source": SOURCE,
            })
        elif kind in STATIC_TYPES:
            dim = body.get("Dimension") or {}
            part = body.get("ReportA") or body.get("ReportB") or {}
            self.static.append({
                "zone": zone, "mmsi": meta.get("MMSI"), "ts": meta.get("time_utc"), "received_at": now,
                "name": (body.get("Name") or part.get("Name") or meta.get("ShipName") or "").strip() or None,
                "imo": body.get("ImoNumber"), "callsign": body.get("CallSign"),
                "ship_type": body.get("Type") or (body.get("ReportB") or {}).get("ShipType"),
                "length_m": (dim.get("A") or 0) + (dim.get("B") or 0) or None,
                "destination": body.get("Destination"), "source": SOURCE,
            })

    def flush(self) -> int:
        """Écrit les messages reçus, un fichier par zone et par type ; retourne le nombre de fichiers écrits. Une
        écriture en échec (disque plein) lève l'erreur et garde tout en mémoire pour l'essai suivant (un fichier déjà
        écrit est réécrit : l'ingestion écarte les doublons), dans la limite de MAX_EN_ATTENTE messages."""
        stamp = datetime.now(timezone.utc)
        written = 0
        for table in ("positions", "statiques"):
            rows = getattr(self, "pos" if table == "positions" else "static")
            if not rows:
                continue
            df = pd.DataFrame(rows)
            for zone, g in df.groupby("zone"):
                folder = OUT / table / f"zone={zone}" / f"date={stamp:%Y-%m-%d}"
                folder.mkdir(parents=True, exist_ok=True)
                # Écriture dans un fichier caché puis renommage : l'ingestion ne lit jamais un fichier incomplet
                tmp = folder / f".{stamp:%H%M%S}.parquet.tmp"
                try:
                    g.drop(columns="zone").to_parquet(tmp, index=False)
                    os.replace(tmp, folder / f"{stamp:%H%M%S}.parquet")
                except Exception:
                    try:
                        tmp.unlink(missing_ok=True)      # un fichier partiel occuperait la dernière place libre
                    except OSError:
                        pass
                    self._trim()
                    raise
                written += 1
        self.pos, self.static = [], []
        return written

    def _trim(self):
        over = len(self.pos) + len(self.static) - MAX_EN_ATTENTE
        if over > 0:
            print(f"ÉCRITURE IMPOSSIBLE depuis trop longtemps : {over} messages les plus anciens abandonnés "
                  f"({MAX_EN_ATTENTE} gardés en mémoire)", flush=True)
            drop_pos = min(over, len(self.pos))
            self.pos = self.pos[drop_pos:]
            self.static = self.static[over - drop_pos:]

    def status(self):
        rate = self.n / max(time.time() - self.started, 1) * 60
        zones = ", ".join(f"{z} {len(s)} navires" for z, s in self.mmsi.items())
        return f"{datetime.now():%H:%M:%S}  {self.n} messages ({rate:.0f} par minute) ; {zones}"


async def collect(api_key):
    import websockets
    buf, stop = Buffer(), asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async def flusher():
        # Incident du 18/10/2026 : disque plein, buf.flush() levait une erreur qui tuait cette tâche en silence ; la
        # lecture du flux continuait, sans plus aucun fichier ni journal, et Docker voyait le conteneur « Up ».
        # Désormais : erreur journalisée à chaque minute, messages gardés pour l'essai suivant, et signes de vie lus
        # par le contrôle de santé (fichier écrit, ou écriture en échec)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), FLUSH_S)
            except asyncio.TimeoutError:
                pass
            # Un span par écriture (chaque minute), racine de sa propre trace
            with traces.span("collecte.ecriture", nouvelle_trace=True) as sp:
                try:
                    n = buf.flush()
                    sp.set_attributes({"collecte.fichiers": n, "collecte.messages_total": buf.n,
                                       "collecte.navires": sum(len(v) for v in buf.mmsi.values())})
                    if n:
                        toucher(OUT / SIGNE_COLLECTE)
                    print(buf.status(), flush=True)
                except Exception as e:
                    waiting = len(buf.pos) + len(buf.static)
                    sp.record_exception(e)
                    sp.set_attribute("collecte.en_attente", waiting)
                    traces.erreur(f"écriture des fichiers Parquet en échec ({type(e).__name__})")
                    print(f"ÉCHEC D'ÉCRITURE des fichiers Parquet ({type(e).__name__}: {str(e)[:200]}) ; "
                          f"{waiting} messages gardés en mémoire, nouvel essai dans {FLUSH_S} s", flush=True)
                    try:
                        toucher(OUT / ECHEC_ECRITURE)
                    except OSError:
                        pass

    async def reader():
        delay = 2
        subscription = {
            "APIKey": api_key,
            "BoundingBoxes": [[[a, b], [c, d]] for a, b, c, d in ZONES.values()],
            "FilterMessageTypes": POSITION_TYPES + STATIC_TYPES,
        }
        while not stop.is_set():
            try:
                # Appel externe tracé : la connexion et l'abonnement (la clé n'est jamais un attribut)
                with traces.span("aisstream.connexion", nouvelle_trace=True,
                                 **{"peer.service": "AISStream", "aisstream.zones": len(ZONES)}):
                    ws = await websockets.connect(URL, ping_interval=20, max_size=2**22)
                    await ws.send(json.dumps(subscription))   # à envoyer dans les 3 secondes
                print("Connecté à AISStream", flush=True)
                delay = 2
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if "error" in msg:
                            with traces.span("aisstream.erreur", nouvelle_trace=True, **{"peer.service": "AISStream"}):
                                traces.erreur(str(msg["error"])[:200])
                            print("Erreur AISStream :", msg["error"], flush=True)
                            break
                        buf.add(msg)
                        if stop.is_set():
                            break
                finally:
                    await ws.close()
            except Exception as e:
                with traces.span("aisstream.coupure", nouvelle_trace=True,
                                 **{"peer.service": "AISStream", "aisstream.nouvel_essai_s": delay}) as sp:
                    sp.record_exception(e)
                    traces.erreur(f"connexion perdue ({e.__class__.__name__})")
                print(f"Connexion perdue ({e.__class__.__name__}), nouvel essai dans {delay} s", flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    tasks = [asyncio.create_task(flusher()), asyncio.create_task(reader())]
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait([stopper, *tasks], return_when=asyncio.FIRST_COMPLETED)
    for t in tasks + [stopper]:
        t.cancel()
    died = [t for t in done if t is not stopper]
    try:
        buf.flush()
    except Exception as e:
        print(f"Dernières données NON écrites ({type(e).__name__})", flush=True)
    if died:
        # Une tâche interne s'est arrêtée seule : jamais de collecte à moitié vivante, le processus s'arrête et Docker
        # le relance (restart: unless-stopped)
        err = died[0].exception() if not died[0].cancelled() else None
        print(f"ARRÊT : tâche de collecte interrompue ({type(err).__name__ if err else 'sans erreur'}: {err})", flush=True)
        raise SystemExit(1)
    print("Arrêt, dernières données écrites dans", OUT)


def report():
    pos_dir, st_dir = OUT / "positions", OUT / "statiques"
    if not pos_dir.exists():
        raise SystemExit("Aucune donnée : lancer d'abord la collecte")
    pos = pd.read_parquet(pos_dir)
    st = pd.read_parquet(st_dir) if st_dir.exists() else pd.DataFrame(columns=["mmsi", "ship_type", "zone"])
    pos["t"] = pos.ts.map(parse_time)
    pos["received_at"] = pd.to_datetime(pos.received_at, utc=True)
    pos["lag_s"] = (pos.received_at - pos.t).dt.total_seconds()
    types = st.dropna(subset=["ship_type"]).groupby("mmsi").ship_type.last()
    pos["ship_type"] = pos.mmsi.map(types)
    pos["cargo_tanker"] = pos.ship_type.between(70, 89)

    # Continuité : intervalles entre messages successifs d'un navire de classe A en route
    pos = pos.sort_values(["mmsi", "t"])
    pos["dt"] = pos.groupby("mmsi").t.diff().dt.total_seconds()
    moving = pos[(~pos.class_b) & (pos.sog >= 1) & pos.dt.between(0, 7200)]

    rows = []
    for zone, g in pos.groupby("zone", observed=True):
        m = moving[moving.zone == zone]
        hours = (g.t.max() - g.t.min()).total_seconds() / 3600
        rows.append({
            "zone": zone,
            "heures": round(hours, 1),
            "messages": len(g),
            "messages_par_heure": round(len(g) / hours) if hours >= 0.1 else None,
            "navires": g.mmsi.nunique(),
            "cargos_petroliers": g[g.cargo_tanker].mmsi.nunique(),
            "retard_median_s": round(float(g.lag_s.median()), 1),
            "retard_p90_s": round(float(g.lag_s.quantile(0.9)), 1),
            "continuite_3min": round((m.dt <= 180).mean(), 3) if len(m) else None,
        })
    print(pd.DataFrame(rows).to_string(index=False))

    # Carte de couverture : cellules de 0,1 degré, continuité par cellule
    moving = moving.assign(cx=(moving.lon // 0.1) * 0.1, cy=(moving.lat // 0.1) * 0.1)
    cells = moving.groupby(["zone", "cx", "cy"], observed=True).agg(
        paires=("dt", "size"), navires=("mmsi", "nunique"), continuite=("dt", lambda s: (s <= 180).mean()))
    path = OUT / "couverture_cellules.csv"
    cells.reset_index().to_csv(path, index=False)
    fiables = cells[(cells.paires >= 50) & (cells.continuite >= 0.95)]
    print(f"\n{len(cells)} cellules observées, {len(fiables)} fiables (50 intervalles, 95 % sous 3 min) ; détail dans {path}")


def main():
    ap = argparse.ArgumentParser(description="Collecte AIS en direct (AISStream) et rapport de couverture")
    ap.add_argument("command", choices=["collect", "report"])
    args = ap.parse_args()
    if args.command == "collect":
        key = os.environ.get("AISSTREAM_API_KEY")
        if not key:
            raise SystemExit("Définir AISSTREAM_API_KEY")
        traces.init("mars-collecte")
        try:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / VERSION_COLLECTE).write_text(traces.version())    # relevée par taches (signe de vie, version)
        except OSError:
            pass
        asyncio.run(collect(key))
    else:
        report()


if __name__ == "__main__":
    main()
