"""Chargement en continu de la collecte AISStream (fichiers Parquet de scripts/ais_live.py) dans la base.

Le Parquet reste l'archive brute, complète ; la base reçoit des trajectoires allégées (mars.ais.live.Thinner) et un
référentiel des navires : identité par MMSI, OMI vérifié par sa clé, et historique des noms, indicatifs, types et
pavillons (table vessel_identities). Chaque fichier est chargé dans une transaction qui l'inscrit au registre
ingested_files : un fichier est chargé une fois et une seule, même après un arrêt brutal.
"""
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg

from mars.ais.live import (Thinner, clean_positions, clean_text, parse_times, ship_type_label, valid_imo,
                           zones_extent)
from mars.ais.mid import flag_of

MIN_FILE_AGE_S = 5           # un fichier plus récent est peut être encore en cours d'écriture
WATCH_REFRESH_S = 60         # rafraîchissement de vessel_watch au plus une fois par minute
IDENTITY_FIELDS = ("name", "imo", "callsign", "ship_type_code")


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


class Ingestor:
    def __init__(self, conn: psycopg.Connection, root: Path, params: dict):
        self.conn, self.root = conn, root
        self.thin = Thinner(moving_s=params["moving_interval_s"], stopped_s=params["stopped_interval_s"],
                            stopped_kn=params["stopped_speed_kn"], moving_kn=params["moving_speed_kn"])
        self.vessel_ids: dict[int, int] = {}
        self.identity: dict[int, dict | None] = {}
        self.touched_days: set = set()
        self.watch_dirty, self.watch_refreshed = True, 0.0

    # Démarrage

    def seed(self) -> int:
        """Dernier point conservé de chaque navire vu depuis deux heures : l'allègement reprend sans trou ni
        doublon après un redémarrage."""
        rows = self.conn.execute(
            """SELECT DISTINCT ON (p.vessel_id) v.mmsi, p.vessel_id, p.ts, p.sog_kn, p.nav_status
               FROM positions p JOIN vessels v ON v.id = p.vessel_id
               WHERE p.ts > now() - interval '2 hours'
               ORDER BY p.vessel_id, p.ts DESC""").fetchall()
        for mmsi, vid, ts, sog, status in rows:
            self.vessel_ids[mmsi] = vid
            self.thin.seed(mmsi, pd.Timestamp(ts), _num(sog), None if status is None else float(status))
        return len(rows)

    # Fichiers à charger

    def _folders(self, recent_only: bool) -> list[str]:
        today = datetime.now(timezone.utc).date()
        recent = {f"date={today - timedelta(days=k):%Y-%m-%d}" for k in (0, 1)}
        out = []
        for kind in ("statiques", "positions"):
            for d in sorted((self.root / kind).glob("zone=*/date=*")):
                if d.is_dir() and (not recent_only or d.name in recent):
                    out.append(str(d.relative_to(self.root)))
        return out

    def pending(self, recent_only: bool = True) -> list[tuple[str, str]]:
        folders = self._folders(recent_only)
        if not folders:
            return []
        done: dict[str, set] = {}
        for folder, name in self.conn.execute(
                "SELECT folder, name FROM ingested_files WHERE folder = ANY(%s)", (folders,)):
            done.setdefault(folder, set()).add(name)
        limit = time.time() - MIN_FILE_AGE_S
        out = []
        for folder in folders:
            seen = done.get(folder, set())
            for f in (self.root / folder).glob("*.parquet"):
                if f.name.startswith(".") or f.name in seen or f.stat().st_mtime > limit:
                    continue
                out.append((folder, f.name))
        # Ordre chronologique (date, heure du fichier), statiques avant positions d'un même instant
        return sorted(out, key=lambda x: (x[0].rsplit("date=", 1)[1], x[1], not x[0].startswith("statiques")))

    # Navires

    def _vessels(self, cur, mmsis, first_ts: dict, ais_class: dict | None = None) -> dict[int, int]:
        """Identifiant de chaque MMSI, en créant les navires inconnus (pavillon déduit du MMSI)."""
        mmsis = [int(m) for m in mmsis]
        missing = [m for m in mmsis if m not in self.vessel_ids]
        if missing:
            for mmsi, vid in cur.execute("SELECT mmsi, max(id) FROM vessels WHERE mmsi = ANY(%s) GROUP BY mmsi",
                                         (missing,)):
                self.vessel_ids[mmsi] = vid
            for m in missing:
                if m in self.vessel_ids:
                    continue
                cls = (ais_class or {}).get(m)
                self.vessel_ids[m] = cur.execute(
                    "INSERT INTO vessels (mmsi, flag, ais_class, first_seen, last_seen) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (m, flag_of(m), cls, first_ts[m].to_pydatetime(), first_ts[m].to_pydatetime())).fetchone()[0]
                self.watch_dirty = True       # un nouveau MMSI peut figurer sur une liste
        return {m: self.vessel_ids[m] for m in mmsis}

    def _current_identity(self, cur, vid: int) -> dict | None:
        if vid not in self.identity:
            r = cur.execute(
                "SELECT id, name, imo, callsign, ship_type_code, flag FROM vessel_identities "
                "WHERE vessel_id = %s ORDER BY last_seen DESC LIMIT 1", (vid,)).fetchone()
            self.identity[vid] = None if r is None else dict(zip(("id", "name", "imo", "callsign",
                                                                 "ship_type_code", "flag"), r))
        return self.identity[vid]

    # Chargement d'un fichier

    def ingest_positions(self, folder: str, name: str) -> dict:
        return self.load_positions(pd.read_parquet(self.root / folder / name), ledger=(folder, name))

    def load_positions(self, raw: pd.DataFrame, ledger: tuple[str, str] | None = None) -> dict:
        """Nettoie, allège et charge un lot de positions dans une transaction ; inscrit le fichier d'origine au
        registre s'il est donné (ingestion en direct), pas pour un rechargement depuis l'archive."""
        df, stats = clean_positions(raw, pd.Timestamp.now(tz="UTC"))
        backup = {int(m): self.thin.last.get(int(m)) for m in df.mmsi.unique()}
        kept, reasons = [], {}
        for r in df.itertuples(index=False):
            ok, why = self.thin.keep(int(r.mmsi), r.t, _num(r.sog), _num(r.nav_status))
            reasons[why] = reasons.get(why, 0) + 1
            if ok:
                kept.append(r)
        stats.update(reasons)
        stats["conserves"] = len(kept)
        try:
            with self.conn.transaction(), self.conn.cursor() as cur:
                if kept:
                    k = pd.DataFrame(kept)
                    first = k.groupby("mmsi").t.min().to_dict()
                    cls = k.groupby("mmsi").class_b.last().map({True: "B", False: "A"}).to_dict()
                    ids = self._vessels(cur, list(first), first, cls)
                    with cur.copy("COPY positions (vessel_id, ts, geom, sog_kn, cog_deg, heading_deg, nav_status) "
                                  "FROM STDIN") as cp:
                        for r in kept:
                            cp.write_row((ids[int(r.mmsi)], r.t.to_pydatetime(), f"SRID=4326;POINT({r.lon} {r.lat})",
                                          _num(r.sog), _num(r.cog),
                                          None if _num(r.heading) is None else int(r.heading),
                                          None if _num(r.nav_status) is None else int(r.nav_status)))
                    agg = k.groupby("mmsi").agg(t0=("t", "min"), t1=("t", "max"))
                    cur.execute(
                        """UPDATE vessels v SET first_seen = least(v.first_seen, x.t0),
                                                last_seen = greatest(v.last_seen, x.t1),
                                                ais_class = coalesce(x.cls, v.ais_class)
                           FROM unnest(%s::bigint[], %s::timestamptz[], %s::timestamptz[], %s::text[])
                                AS x(id, t0, t1, cls)
                           WHERE v.id = x.id""",
                        ([ids[int(m)] for m in agg.index], [t.to_pydatetime() for t in agg.t0],
                         [t.to_pydatetime() for t in agg.t1], [cls.get(m) for m in agg.index]))
                    self.touched_days.update(k.t.dt.date.unique())
                if ledger:
                    cur.execute("INSERT INTO ingested_files (folder, name, rows_read, rows_kept) "
                                "VALUES (%s, %s, %s, %s)", (*ledger, len(raw), len(kept)))
        except Exception:
            for m, v in backup.items():        # l'allègement revient à l'état d'avant le fichier
                if v is None:
                    self.thin.last.pop(m, None)
                else:
                    self.thin.last[m] = v
            raise
        return stats

    def ingest_statics(self, folder: str, name: str) -> dict:
        raw = pd.read_parquet(self.root / folder / name)
        df = raw.copy()
        df["t"] = parse_times(df["ts"])
        df["mmsi"] = pd.to_numeric(df["mmsi"], errors="coerce")
        df = df[df.mmsi.between(100_000_000, 999_999_999) & df.t.notna()].sort_values("t")
        df["mmsi"] = df.mmsi.astype("int64")
        stats = {"statiques": len(raw), "changements_identite": 0}
        try:
            with self.conn.transaction(), self.conn.cursor() as cur:
                ids = self._vessels(cur, list(df.mmsi.unique()), df.groupby("mmsi").t.min().to_dict())
                for r in df.itertuples(index=False):
                    vid = ids[int(r.mmsi)]
                    code = _num(r.ship_type)
                    code = int(code) if code is not None and 1 <= code <= 99 else None
                    length = _num(r.length_m)
                    new = {"name": clean_text(r.name), "imo": valid_imo(r.imo), "callsign": clean_text(r.callsign),
                           "ship_type_code": code}
                    if all(v is None for v in new.values()):
                        continue
                    stats["changements_identite"] += self._record_identity(cur, vid, r.mmsi, r.t, new)
                    ident = self.identity[vid]
                    cur.execute(
                        """UPDATE vessels SET name = %s, imo = %s, callsign = %s, ship_type_code = %s,
                                  ship_type = coalesce(%s, ship_type), flag = coalesce(flag, %s),
                                  length_m = coalesce(%s, length_m), destination = coalesce(%s, destination)
                           WHERE id = %s""",
                        (ident["name"], ident["imo"], ident["callsign"], ident["ship_type_code"],
                         ship_type_label(ident["ship_type_code"]), flag_of(r.mmsi),
                         float(length) if length and 0 < length < 600 else None, clean_text(r.destination), vid))
                cur.execute("INSERT INTO ingested_files (folder, name, rows_read, rows_kept) VALUES (%s, %s, %s, %s)",
                            (folder, name, len(raw), len(df)))
        except Exception:
            self.identity.clear()         # le cache pourrait refléter des lignes annulées
            raise
        return stats

    def _record_identity(self, cur, vid: int, mmsi: int, t, new: dict) -> int:
        """Met à jour l'identité courante du navire. Un champ renseigné qui contredit la valeur connue fait passer à
        une autre identité : une identité déjà vue pour ce navire est reprise (sa période s'étend), sinon une ligne
        est créée. Les alternances ne multiplient donc pas les lignes : le MMSI 227000000, partagé par plusieurs
        bâtiments de la Marine nationale, ou un navire qui émet tour à tour « MUTIN » et « FS MUTIN », donnent deux
        lignes aux périodes entrelacées. Un champ jusque là vide complète la ligne courante (les navires de classe B
        envoient leur nom et leur indicatif dans deux messages distincts). Renvoie 1 pour une identité nouvelle."""
        cur_id = self._current_identity(cur, vid)
        ts = t.to_pydatetime()
        changed = cur_id is None or any(new[f] is not None and cur_id[f] is not None and new[f] != cur_id[f]
                                        for f in IDENTITY_FIELDS)
        if changed:
            merged = {f: new[f] if new[f] is not None else (cur_id or {}).get(f) for f in IDENTITY_FIELDS}
            merged["flag"] = flag_of(mmsi)
            known = cur.execute(
                "SELECT id FROM vessel_identities WHERE vessel_id = %s AND name IS NOT DISTINCT FROM %s "
                "AND imo IS NOT DISTINCT FROM %s AND callsign IS NOT DISTINCT FROM %s "
                "AND ship_type_code IS NOT DISTINCT FROM %s ORDER BY last_seen DESC LIMIT 1",
                (vid, merged["name"], merged["imo"], merged["callsign"], merged["ship_type_code"])).fetchone()
            if known:
                merged["id"] = known[0]
                cur.execute("UPDATE vessel_identities SET last_seen = greatest(last_seen, %s), "
                            "first_seen = least(first_seen, %s), messages = messages + 1 WHERE id = %s",
                            (ts, ts, known[0]))
            else:
                merged["id"] = cur.execute(
                    "INSERT INTO vessel_identities (vessel_id, name, imo, callsign, ship_type_code, flag, first_seen, "
                    "last_seen) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (vid, merged["name"], merged["imo"], merged["callsign"], merged["ship_type_code"], merged["flag"],
                     ts, ts)).fetchone()[0]
            self.identity[vid] = merged
            if cur_id is None or merged["imo"] != cur_id.get("imo"):
                self.watch_dirty = True
            return 0 if cur_id is None or known else 1
        fill = {f: new[f] for f in IDENTITY_FIELDS if new[f] is not None and cur_id[f] is None}
        cur.execute(
            "UPDATE vessel_identities SET last_seen = greatest(last_seen, %s), messages = messages + 1, "
            "name = coalesce(name, %s), imo = coalesce(imo, %s), callsign = coalesce(callsign, %s), "
            "ship_type_code = coalesce(ship_type_code, %s) WHERE id = %s",
            (ts, new["name"], new["imo"], new["callsign"], new["ship_type_code"], cur_id["id"]))
        cur_id.update(fill)
        if "imo" in fill:
            self.watch_dirty = True
        return 0

    # Tâches de fin de cycle

    def update_days(self):
        """Journées disponibles (ais_days), sur lesquelles s'appuient le rejeu et les analyses radar."""
        lon0, lat0, lon1, lat1 = zones_extent()
        for day in sorted(self.touched_days):
            self.conn.execute(
                """INSERT INTO ais_days (day, messages, vessels, lon_min, lat_min, lon_max, lat_max)
                   SELECT %(d)s::date, count(*), count(DISTINCT vessel_id), %(lon0)s, %(lat0)s, %(lon1)s, %(lat1)s
                   FROM positions
                   WHERE ts >= (%(d)s::date)::timestamp AT TIME ZONE 'UTC'
                     AND ts < (%(d)s::date + 1)::timestamp AT TIME ZONE 'UTC'
                   ON CONFLICT (day) DO UPDATE SET messages = EXCLUDED.messages, vessels = EXCLUDED.vessels,
                       lon_min = EXCLUDED.lon_min, lat_min = EXCLUDED.lat_min, lon_max = EXCLUDED.lon_max,
                       lat_max = EXCLUDED.lat_max, imported_at = now()""",
                {"d": day, "lon0": lon0, "lat0": lat0, "lon1": lon1, "lat1": lat1})
        self.touched_days.clear()

    def refresh_watch(self, force: bool = False) -> bool:
        if not force and (not self.watch_dirty or time.time() - self.watch_refreshed < WATCH_REFRESH_S):
            return False
        self.conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY vessel_watch")
        self.watch_dirty, self.watch_refreshed = False, time.time()
        return True

    def run_once(self, recent_only: bool = True) -> dict:
        totals: dict = {"fichiers": 0}
        for folder, name in self.pending(recent_only):
            try:
                s = (self.ingest_statics if folder.startswith("statiques") else self.ingest_positions)(folder, name)
            except (OSError, ValueError) as e:     # fichier illisible : on réessaiera au cycle suivant
                print(f"Fichier ignoré pour l'instant, {folder}/{name} : {e}", flush=True)
                continue
            totals["fichiers"] += 1
            for k, v in s.items():
                totals[k] = totals.get(k, 0) + v
        if totals["fichiers"]:
            self.update_days()
        self.refresh_watch()
        return totals
