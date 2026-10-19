"""Traces OpenTelemetry des travailleurs, en bibliothèque standard seule (étape D2).

Pourquoi pas le kit officiel : les images d'analyse sont publiques et figées (AI2 pour Sentinel 1 en Python 3.8,
allenai pour VIIRS en Python 3.10). Le dernier kit qui accepte Python 3.8 est la version 1.33.1, et tout
exportateur OTLP officiel exige protobuf 5 ou plus, alors que ces deux images embarquent protobuf 4 (requis par
google-api-core et wandb) : l'installer à chaque lancement, sur une instance éphémère, risquerait de casser la
chaîne d'analyse, demanderait PyPI et coûterait des minutes facturées. Ce module écrit donc directement le format
OTLP en JSON par HTTP (spécification OTLP/HTTP, identifiants en hexadécimal), comme mars/r2_signature.py le fait pour
la signature AWS ; il est éprouvé contre le vrai collecteur (scripts/verifier_images.sh traces).

Le travailleur reçoit le contexte de sa trace (TRACEPARENT, posé par travailleurs/demarrage.sh, sinon celui de la
tâche) ; ses spans rejoignent la trace du lancement sur le serveur. Envoi au serveur par le réseau privé, avec le
jeton de l'exécution : nginx (port 8090) fait vérifier le jeton par l'API, puis relaie au collecteur. Aucun secret
dans un attribut : les clés au nom sensible sont retirées, les adresses perdent leur chaîne de requête.
Compatible Python 3.8 : pas d'écriture postérieure.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from contextlib import contextmanager

SERVICE = "mars-travailleur"
CLE_SENSIBLE = re.compile(r"(?i)(authorization|cookie|token|jeton|secret|password|mot_de_passe|api_?key|access_?key|"
                          r"credential|signature)")
URL = re.compile(r"(?i)\b(https?://[^\s?#\"']+)[?#][^\s\"']*")
MAX_SPANS = 2000                       # en attente au plus, si le serveur est injoignable


def _id(n: int) -> str:
    return os.urandom(n).hex()


def _valeur(v) -> dict:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": URL.sub(r"\1", str(v))[:1000]}


def attributs(d: dict) -> list:
    return [{"key": k, "value": _valeur(v)} for k, v in d.items() if v is not None and not CLE_SENSIBLE.search(k)]


class Span:
    def __init__(self, traceur: "Traceur", nom: str, parent: str | None, attrs: dict):
        self.traceur, self.nom, self.parent, self.attrs = traceur, nom, parent, dict(attrs)
        self.id, self.debut, self.statut, self.evenements = _id(8), time.time_ns(), None, []

    def set(self, **kw):
        self.attrs.update(kw)

    def erreur(self, motif: str):
        self.statut = {"code": 2, "message": URL.sub(r"\1", str(motif))[:300]}

    def exception(self, e: BaseException):
        self.evenements.append({"timeUnixNano": str(time.time_ns()), "name": "exception", "attributes": attributs(
            {"exception.type": type(e).__name__, "exception.message": str(e)[:500]})})
        self.erreur(f"{type(e).__name__}: {e}")

    def fin(self) -> dict:
        d = {"traceId": self.traceur.trace_id, "spanId": self.id, "name": self.nom, "kind": 1,
             "startTimeUnixNano": str(self.debut), "endTimeUnixNano": str(time.time_ns()),
             "attributes": attributs(self.attrs), "events": self.evenements, "status": self.statut or {"code": 1}}
        if self.parent:
            d["parentSpanId"] = self.parent
        return d


class Traceur:
    """Spans d'un travailleur. `traceparent` : contexte W3C reçu (00-<trace>-<span parent>-01) ; sans lui, une trace
    neuve. `envoi` : adresse OTLP (…/traces du serveur) ; `jeton` : jeton de l'exécution."""

    def __init__(self, traceparent: str | None, envoi: str | None, jeton: str | None, ressource: dict | None = None,
                 opener=None):
        m = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}", (traceparent or "").strip())
        self.trace_id, self.racine = (m.group(1), m.group(2)) if m else (_id(16), None)
        self.envoi, self.jeton, self.opener = envoi, jeton, opener or urllib.request.urlopen
        self.ressource = {"service.name": SERVICE, "service.namespace": "mars-c2", **(ressource or {})}
        self.pile: list = []
        self.finis: list = []
        self.envoyes = self.echecs = 0

    @contextmanager
    def span(self, nom: str, **attrs):
        s = Span(self, nom, self.pile[-1].id if self.pile else self.racine, attrs)
        self.pile.append(s)
        try:
            yield s
        except BaseException as e:
            s.exception(e)
            raise
        finally:
            self.pile.pop()
            self.finis.append(s.fin())
            del self.finis[:-MAX_SPANS]

    def traceparent(self) -> str:
        sid = self.pile[-1].id if self.pile else (self.racine or _id(8))
        return "00-" + self.trace_id + "-" + sid + "-01"

    def corps(self, spans: list) -> bytes:
        return json.dumps({"resourceSpans": [{
            "resource": {"attributes": attributs(self.ressource)},
            "scopeSpans": [{"scope": {"name": "mars.travailleur"}, "spans": spans}]}]}).encode()

    def envoyer(self) -> bool:
        """Envoie les spans terminés ; en cas d'échec, ils sont gardés pour l'envoi suivant. Jamais d'exception : une
        trace perdue ne doit pas arrêter une analyse."""
        if not self.finis or not self.envoi:
            return True
        lot = list(self.finis)
        req = urllib.request.Request(self.envoi, data=self.corps(lot), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + (self.jeton or "")})
        try:
            with self.opener(req, timeout=15) as r:
                ok = 200 <= r.status < 300
        except Exception:
            ok = False
        if ok:
            del self.finis[:len(lot)]
            self.envoyes += len(lot)
        else:
            self.echecs += 1
        return ok


def depuis_tache(task: dict, **ressource) -> Traceur:
    """Traceur d'un travailleur : contexte TRACEPARENT (span « analyse » du script de démarrage), sinon celui de la
    tâche ; envoi à <retour>/traces avec le jeton de l'exécution."""
    tp = os.environ.get("TRACEPARENT") or task.get("traceparent")
    envoi = (task["retour"].rstrip("/") + "/traces") if task.get("retour") else None
    return Traceur(tp, envoi, task.get("jeton"), ressource)
