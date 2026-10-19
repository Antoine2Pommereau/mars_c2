"""Contrôle de santé Docker de la collecte et de l'ingestion, avec redémarrage automatique (après l'incident du 18/10/2026).

Docker (hors Swarm) ne redémarre jamais un conteneur « unhealthy » : il le signale, c'est tout. Les outils qui le font
(autoheal…) demandent le socket Docker, c'est à dire les droits de root sur l'hôte : écarté. Ici, le contrôle de santé
arrête lui même le processus figé ; le conteneur s'arrête avec lui et la politique « restart: unless-stopped » le
relance. Il faut « init: true » dans Compose : le noyau ignore un signal envoyé, depuis son propre conteneur, au
processus numéro 1 qui ne l'a pas prévu (Python, sans gestionnaire) ; avec init, le service n'est plus le numéro 1.

    python scripts/sante.py collecte     sain si un fichier Parquet a été écrit depuis SEUIL_COLLECTE_MIN (5) minutes
    python scripts/sante.py ingestion    sain si la boucle d'ingestion a tourné depuis SEUIL_COLLECTE_MIN minutes

Collecte figée (aucun fichier, et pas d'erreur d'écriture en cours) : redémarrée. Écritures en échec (disque plein) :
pas de redémarrage, qui perdrait les messages gardés en mémoire en attendant la place ; le contrôle échoue (conteneur
« unhealthy ») et l'alerte disque prévient. Ingestion : redémarrée si sa boucle est figée ; en échec répété (disque
plein), elle continue de tourner et de journaliser, l'alerte « ingestion en échec » prévient.
Chaque redémarrage est consigné dans task_runs (tâche redemarrage_collecte ou redemarrage_ingestion, avec son motif),
et dans le journal du conteneur.
"""
import json
import os
import signal
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mars.supervision import SIGNE_COLLECTE, age_s, seuil  # noqa: E402

LIVE = Path(os.environ.get("AIS_LIVE_DIR", Path(__file__).resolve().parents[1] / "data" / "ais_live"))
ECHEC_ECRITURE = ".echec_ecriture_collecte"          # touché par la collecte quand une écriture échoue
BOUCLE_INGESTION = "/tmp/boucle_ingestion"           # touché à chaque tour de la boucle d'ingestion
SERVICES = {"collecte": "ais_live.py", "ingestion": "ais_ingest.py"}


def processus(motif: str) -> list[int]:
    """Processus du service dans ce conteneur (ligne de commande), hors de soi même."""
    pids = []
    for d in Path("/proc").glob("[0-9]*"):
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if motif in cmd and "sante.py" not in cmd and int(d.name) != os.getpid():
            pids.append(int(d.name))
    return pids


def demarre_depuis_s(pid: int) -> float:
    """Âge du processus en secondes (/proc/<pid>/stat, champ 22, en tops d'horloge depuis le démarrage)."""
    ticks = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    return uptime - ticks / os.sysconf("SC_CLK_TCK")


def journal(service: str, motif: str, details: dict):
    """Redémarrage consigné dans task_runs (si la base l'accepte : disque plein, elle refuse) et dans le journal du
    conteneur (sortie du processus numéro 1)."""
    line = f"REDÉMARRAGE AUTOMATIQUE de la {service} : {motif}"
    for out in ("/proc/1/fd/1", "/dev/stderr"):
        try:
            with open(out, "w") as f:
                f.write(line + "\n")
            break
        except OSError:
            continue
    if not os.environ.get("DATABASE_URL"):
        return
    try:
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=5) as c:
            c.execute("INSERT INTO task_runs (task, run_day, status, finished_at, details) VALUES (%s, %s, 'echec', "
                      "now(), %s)", (f"redemarrage_{service}", date.today(), json.dumps({"motif": motif, **details})))
    except Exception as e:                     # la base refuse (disque plein) : le journal du conteneur suffit
        print(f"redémarrage non consigné en base ({type(e).__name__})", file=sys.stderr)


def arreter(pids: list[int], attente_s: float = 15):
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
    fin = time.time() + attente_s
    while time.time() < fin and any(Path(f"/proc/{p}").exists() for p in pids):
        time.sleep(0.5)
    for p in pids:                             # boucle d'événements figée : SIGTERM sans effet
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass


def verifier(service: str, now: float | None = None) -> tuple[bool, str, bool]:
    """(sain, motif, redémarrer)."""
    limite = 60 * seuil("SEUIL_COLLECTE_MIN", 5)
    pids = processus(SERVICES[service])
    if not pids:
        return False, "processus absent", False          # le conteneur s'arrête de lui même : Docker le relance
    try:
        depuis = min(demarre_depuis_s(p) for p in pids)
    except (OSError, ValueError, IndexError):
        depuis = limite + 1
    if service == "collecte":
        a = age_s(LIVE / SIGNE_COLLECTE, now)
        if (a is not None and a <= limite) or depuis <= limite:
            return True, "", False
        e = age_s(LIVE / ECHEC_ECRITURE, now)
        if e is not None and e <= 180:
            return False, "écriture des fichiers Parquet en échec (disque plein ?) : pas de redémarrage, les messages " \
                          "restent en mémoire", False
        return False, ("aucun fichier Parquet écrit depuis " +
                       (f"{a / 60:.0f} min" if a is not None else "le démarrage")), True
    a = age_s(BOUCLE_INGESTION, now)
    if (a is not None and a <= limite) or depuis <= limite:
        return True, "", False
    return False, "boucle d'ingestion figée depuis " + (f"{a / 60:.0f} min" if a is not None else "le démarrage"), True


def main() -> int:
    service = sys.argv[1] if len(sys.argv) > 1 else ""
    if service not in SERVICES:
        print("usage : sante.py collecte|ingestion", file=sys.stderr)
        return 2
    sain, motif, redemarrer = verifier(service)
    if sain:
        return 0
    print(motif)
    if redemarrer:
        pids = processus(SERVICES[service])
        journal(service, motif, {"processus": pids})
        arreter(pids)
    return 1


if __name__ == "__main__":
    sys.exit(main())
