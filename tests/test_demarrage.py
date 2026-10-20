"""Script de démarrage des travailleurs (travailleurs/demarrage.sh), exécuté pour de vrai par bash avec des commandes
simulées (ip, curl, nvidia-smi, docker, dmesg, shutdown) : contrôle du GPU (lot C4), état « gpu_inutilisable » signalé
sans lancer l'analyse, état « echec » signalé avec l'étape et le motif pour toute sortie en échec (essai 26 du
19/10/2026), et spans des étapes. Ignoré sans `gdate` ou `date +%N` (nanosecondes des spans)."""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATE = shutil.which("gdate") or "date"
MAC = "02:00:00:aa:bb:cc"
TP = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"

pytestmark = pytest.mark.skipif(
    not subprocess.run([DATE, "+%N"], capture_output=True, text=True).stdout.strip().isdigit(),
    reason="date sans nanosecondes (installer coreutils : gdate)")

NVIDIA_OK = r'''#!/bin/bash
if [ "$1" = "--query-gpu=name,driver_version,memory.total" ]; then echo "NVIDIA L4, 570.124.06, 23034 MiB"; exit 0; fi
echo "| NVIDIA-SMI 570.124.06   Driver Version: 570.124.06   CUDA Version: 12.8 |"
'''
NVIDIA_KO = r'''#!/bin/bash
echo "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver. Make sure that the latest NVIDIA driver is installed and running."
exit 9
'''


def _curl_r2(tmp: Path, code_r2: str) -> str:
    """Faux curl : l'état « acces » rend une adresse R2, que R2 sert (206) ou refuse (403 et son XML)."""
    return ('#!/bin/bash\nurl=""; data=""; out=""\nwhile [ $# -gt 0 ]; do case "$1" in -d|--data-binary) data="$2"; '
            'shift 2;; -o) out="$2"; shift 2;; http*) url="$1"; shift;; *) shift;; esac; done\n'
            f'echo "$url $data" >> {tmp}/curl.log\n'
            'case "$data" in @*) cp "${data#@}" ' + f'{tmp}/envoi_$(date +%s%N).json;; esac\n'
            'case "$data" in *acces*) printf \'{"adresses": [{"nom": "poids projects/a/best.ckpt", '
            '"url": "https://r2.test/mars-c2-modeles/a?X-Amz-Signature=abc"}]}\\n200\'; exit 0;; esac\n'
            'case "$url" in https://r2.test/*) '
            + (f'echo -n x > "$out"; echo -n {code_r2}' if code_r2 == "206" else
               'echo -n \'<?xml version="1.0"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message>'
               f'</Error>\' > "$out"; echo -n {code_r2}') + '; exit 0;; esac\necho -n 200\n')


def _outils(tmp: Path, nvidia: str, docker_code: int, docker_sortie: str, curl: str | None = None) -> Path:
    b = tmp / "bin"
    b.mkdir()
    outils = {
        "ip": '#!/bin/bash\n[ "$1 $2" = "-4 -br" ] && echo "eth9 UP 172.16.8.9/22"\nexit 0\n',
        "curl": ('#!/bin/bash\nurl=""; data=""\nwhile [ $# -gt 0 ]; do case "$1" in -d|--data-binary) data="$2"; '
                 'shift 2;; http*) url="$1"; shift;; *) shift;; esac; done\n'
                 f'echo "$url $data" >> {tmp}/curl.log\n'
                 'case "$data" in @*) cp "${data#@}" ' + f'{tmp}/envoi_$(date +%s%N).json;; esac\necho -n 200\n'),
        "nvidia-smi": nvidia,
        **({"curl": curl} if curl else {}),
        "docker": (f'#!/bin/bash\necho "$*" >> {tmp}/docker.log\n'
                   f'[ "$1" = "run" ] && {{ echo "{docker_sortie}"; exit {docker_code}; }}\nexit 0\n'),
        "dmesg": '#!/bin/bash\necho "[   12.3] NVRM: The NVIDIA Tesla P100 GPU is not supported by the open kernel modules"\n',
        "shutdown": f'#!/bin/bash\necho "$*" >> {tmp}/shutdown.log\n',
        "date": f'#!/bin/bash\nexec {DATE} "$@"\n',
    }
    for nom, texte in outils.items():
        f = b / nom
        f.write_text(texte)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return b


def _lancer(tmp: Path, gpu: str = "--gpus all --shm-size=16g", nvidia: str = NVIDIA_OK, docker_code: int = 0,
            docker_sortie: str = "analyse terminée", code_r2: str | None = None) -> dict:
    mars = tmp / "mars"
    mars.mkdir()
    shutil.copy(ROOT / "travailleurs" / "traces.sh", mars / "traces.sh")
    (mars / "env").write_text("")
    (mars / "demarrage.env").write_text(
        f'RETOUR="http://172.16.8.2:8090/api/travailleurs/26"\nJETON="j"\nMAC="{MAC}"\nIMAGE="img"\n'
        f'SCRIPT="sentinel.py"\nGPU="{gpu}"\nTRACEPARENT="{TP}"\n')
    net = tmp / "net" / "eth9"
    net.mkdir(parents=True)
    (net / "address").write_text(MAC + "\n")
    b = _outils(tmp, nvidia, docker_code, docker_sortie, _curl_r2(tmp, code_r2) if code_r2 else None)
    env = {**os.environ, "PATH": f"{b}:{os.environ['PATH']}", "MARS_DIR": str(mars), "MARS_LOG": str(tmp / "log"),
           "MARS_CONSOLE": "/dev/null", "MARS_NET_DIR": str(tmp / "net"), "SPANS": str(tmp / "spans.json")}
    p = subprocess.run(["bash", str(ROOT / "travailleurs" / "demarrage.sh")], env=env, capture_output=True, text=True,
                       timeout=60)
    curl = (tmp / "curl.log").read_text().splitlines() if (tmp / "curl.log").exists() else []
    etats = [json.loads(ligne.split(" ", 1)[1]) for ligne in curl if ligne.split(" ")[0].endswith("/etat")]
    spans = []
    for f in sorted(tmp.glob("envoi_*.json")):
        try:
            for r in json.loads(f.read_text())["resourceSpans"]:
                spans += [s for sc in r["scopeSpans"] for s in sc["spans"]]
        except (ValueError, KeyError):
            pass                                         # journal en texte
    docker = (tmp / "docker.log").read_text() if (tmp / "docker.log").exists() else ""
    return {"code": p.returncode, "etats": etats, "spans": {s["name"]: s for s in spans}, "docker": docker,
            "shutdown": (tmp / "shutdown.log").exists(), "log": (tmp / "log").read_text()}


def _attrs(span: dict) -> dict:
    return {a["key"]: a["value"]["stringValue"] for a in span.get("attributes", [])}


def test_nvidia_smi_failure_reports_unusable_gpu_without_docker_run(tmp_path):
    r = _lancer(tmp_path, nvidia=NVIDIA_KO)
    assert r["code"] == 1 and r["shutdown"]
    assert "run " not in r["docker"] and "pull" not in r["docker"]           # ni analyse, ni image de 5 Go
    fin = r["etats"][-1]
    assert fin["etat"] == "gpu_inutilisable" and "couldn't communicate with the NVIDIA driver" in fin["message"]
    assert "open kernel modules" in fin["message"]                           # dmesg nvidia joint
    gpu = r["spans"]["demarrage.controle_gpu"]
    assert gpu["status"]["code"] == 2 and "nvidia-smi (code 9)" in _attrs(gpu)["gpu.message"]
    assert r["spans"]["travailleur.demarrage"]["status"]["code"] == 2


def test_working_gpu_is_described_then_analysed(tmp_path):
    r = _lancer(tmp_path)
    assert r["code"] == 0 and [e["etat"] for e in r["etats"]] == ["reseau", "acces"]
    assert "run --rm --gpus all" in r["docker"]
    a = _attrs(r["spans"]["demarrage.controle_gpu"])
    assert a == {"gpu.nom": "NVIDIA L4", "gpu.pilote": "570.124.06", "gpu.memoire": "23034 MiB", "gpu.cuda": "12.8"}
    assert "GPU : NVIDIA L4" in r["log"]


def test_no_gpu_requested_means_no_gpu_check(tmp_path):
    r = _lancer(tmp_path, gpu="--shm-size=16g", nvidia=NVIDIA_KO)
    assert r["code"] == 0 and "demarrage.controle_gpu" not in r["spans"] and "run --rm --shm-size" in r["docker"]


def test_docker_nvml_error_is_caught_as_unusable_gpu(tmp_path):
    r = _lancer(tmp_path, docker_code=125, docker_sortie="docker: Error response from daemon: failed to create task: "
                "failed to initialize NVML: Driver Not Loaded")
    fin = r["etats"][-1]
    assert fin["etat"] == "gpu_inutilisable" and "NVML: Driver Not Loaded" in fin["message"] and r["shutdown"]


def test_docker_failure_reports_a_machine_failure_at_once(tmp_path):
    """Essai 26 : docker run en 125 sans rien signaler ; désormais l'état echec part aussitôt, avec l'étape."""
    r = _lancer(tmp_path, docker_code=125, docker_sortie="docker: Error response from daemon: no space left on device")
    fin = r["etats"][-1]
    assert fin["etat"] == "echec" and fin["etape"] == "analyse_docker" and "code de sortie 125" in fin["message"]
    assert "no space left" in fin["message"] and r["shutdown"]


def test_analysis_failure_reports_the_analysis_step(tmp_path):
    r = _lancer(tmp_path, docker_code=1, docker_sortie="Traceback: RuntimeError: CUDA out of memory")
    fin = r["etats"][-1]
    assert fin["etat"] == "echec" and fin["etape"] == "analyse" and "CUDA out of memory" in fin["message"]
    assert r["spans"]["demarrage.analyse"]["status"]["code"] == 2


def test_refused_r2_address_stops_the_worker_before_docker(tmp_path):
    """Essai Sentinel 2 du 20/10/2026 (travailleur 29) : 403 AccessDenied découvert après l'image de 7,2 Go ; désormais
    un octet est lu de chaque adresse R2 dès le réseau privé joint, et le refus part au serveur avec la réponse de R2."""
    r = _lancer(tmp_path, gpu="--shm-size=16g", code_r2="403")
    assert r["code"] == 1 and r["shutdown"] and r["docker"] == ""                # ni installation, ni image, ni analyse
    fin = r["etats"][-1]
    assert fin["etat"] == "echec" and fin["etape"] == "controle_r2"
    assert fin["message"] == "poids projects/a/best.ckpt : R2 refuse la lecture (HTTP 403 AccessDenied : Access Denied)"
    assert "Signature" not in json.dumps(r["etats"])                              # jamais l'adresse signée
    sp = r["spans"]["demarrage.controle_r2"]
    assert sp["status"]["code"] == 2 and _attrs(sp)["r2.code"] == "AccessDenied"


def test_readable_r2_address_lets_the_analysis_run(tmp_path):
    r = _lancer(tmp_path, gpu="--shm-size=16g", code_r2="206")
    assert r["code"] == 0 and "pull -q img" in r["docker"] and "run --rm" in r["docker"]
    assert _attrs(r["spans"]["demarrage.controle_r2"])["r2.adresses"] == "1"
    assert "R2 : 1 adresse(s) lisible(s) depuis le travailleur" in r["log"]
