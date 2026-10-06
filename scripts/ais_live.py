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
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ais_live"
URL = "wss://stream.aisstream.io/v0/stream"

# Zones : [lat_min, lon_min, lat_max, lon_max]
ZONES = {
    "bretagne": [47.3, -6.8, 49.6, -3.0],
    "mediterranee": [41.2, 3.0, 43.7, 9.8],
    "manche": [48.4, -5.0, 51.2, 2.6],
    "gascogne": [43.3, -6.0, 47.4, -1.0],
}
POSITION_TYPES = ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"]
STATIC_TYPES = ["ShipStaticData", "StaticDataReport"]
FLUSH_S = 60


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
                "class_b": kind != "PositionReport",
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
                "destination": body.get("Destination"),
            })

    def flush(self):
        stamp = datetime.now(timezone.utc)
        for table, rows in (("positions", self.pos), ("statiques", self.static)):
            if not rows:
                continue
            df = pd.DataFrame(rows)
            for zone, g in df.groupby("zone"):
                folder = OUT / table / f"zone={zone}" / f"date={stamp:%Y-%m-%d}"
                folder.mkdir(parents=True, exist_ok=True)
                g.drop(columns="zone").to_parquet(folder / f"{stamp:%H%M%S}.parquet", index=False)
        self.pos, self.static = [], []

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
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), FLUSH_S)
            except asyncio.TimeoutError:
                pass
            buf.flush()
            print(buf.status(), flush=True)

    async def reader():
        delay = 2
        subscription = {
            "APIKey": api_key,
            "BoundingBoxes": [[[a, b], [c, d]] for a, b, c, d in ZONES.values()],
            "FilterMessageTypes": POSITION_TYPES + STATIC_TYPES,
        }
        while not stop.is_set():
            try:
                async with websockets.connect(URL, ping_interval=20, max_size=2**22) as ws:
                    await ws.send(json.dumps(subscription))   # à envoyer dans les 3 secondes
                    print("Connecté à AISStream", flush=True)
                    delay = 2
                    async for raw in ws:
                        msg = json.loads(raw)
                        if "error" in msg:
                            print("Erreur AISStream :", msg["error"], flush=True)
                            break
                        buf.add(msg)
                        if stop.is_set():
                            break
            except Exception as e:
                print(f"Connexion perdue ({e.__class__.__name__}), nouvel essai dans {delay} s", flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    tasks = [asyncio.create_task(flusher()), asyncio.create_task(reader())]
    await stop.wait()
    for t in tasks:
        t.cancel()
    buf.flush()
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
        asyncio.run(collect(key))
    else:
        report()


if __name__ == "__main__":
    main()
