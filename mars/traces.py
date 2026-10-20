"""Traces OpenTelemetry de MARS C2 (étape D2), communes à l'API, à la collecte, à l'ingestion et aux tâches.

Règle du projet (CLAUDE.md) : toute addition est instrumentée ; chaque tâche, route, appel externe et étape métier
produit des spans, avec les attributs utiles et sans donnée sensible.

* `init(service)` : fournisseur de traces (nom du service, empreinte du commit de l'image), envoi OTLP par HTTP au
  collecteur si OTEL_EXPORTER_OTLP_ENDPOINT est défini (sur le serveur : http://otel:4318) ; sans collecteur (Mac,
  tests), les spans existent quand même : leurs identifiants sont consignés en base (task_runs, travailleurs…).
* `span(nom, **attributs)` : span métier ; une erreur y est enregistrée et marque le span en erreur.
* Appels HTTP sortants par requests : tracés automatiquement, avec le service externe nommé (peer.service).
* Données sensibles : tout span passe par `Nettoyeur` avant l'envoi (clés, jetons, mots de passe, adresses signées,
  chaînes de requête, sujet ntfy, chemins qui portent un MMSI) ; le collecteur filtre une seconde fois
  (otel/collecteur.yaml). Ni nom de navire ni position dans un attribut : identifiants internes seulement.
* Journaux : trace_id et span_id ajoutés à chaque ligne écrite pendant un span (print et logging).
* `en_base(conn)` : l'identifiant de trace est donné à la session PostgreSQL (mars.trace_id) ; un déclencheur l'écrit
  dans les détails de chaque alerte créée ou mise à jour (db/init/27_traces.sql).
"""
from __future__ import annotations

import logging
import os
import re
import sys
from contextlib import asynccontextmanager, contextmanager
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_tracer = trace.get_tracer("mars")
_provider = None

# Services externes nommés dans les spans (peer.service), d'après la fin du nom d'hôte
EXTERNES = {
    "aisstream.io": "AISStream", "dataspace.copernicus.eu": "Copernicus", "copernicus.eu": "Copernicus",
    "esa.int": "ESA", "earthdata.nasa.gov": "NASA", "nasa.gov": "NASA", "api.scaleway.com": "Scaleway",
    "r2.cloudflarestorage.com": "R2", "opensanctions.org": "OpenSanctions", "github.com": "GitHub",
    "githubusercontent.com": "GitHub", "vesselfinder.com": "VesselFinder", "vesselfinder.net": "VesselFinder",
    "ntfy.sh": "ntfy", "naciscdn.org": "Natural Earth", "ngdc.noaa.gov": "NOAA", "soest.hawaii.edu": "GSHHG",
    "emodnet-humanactivities.eu": "EMODnet", "sentinels.copernicus.eu": "ESA",
}
# Nom lisible d'un appel externe : « MÉTHODE opération » ; motif sur l'hôte et le chemin (sans chaîne de requête),
# service distant, opération ({1} : premier groupe du motif). Premier motif reconnu gagnant ; tests/test_traces.py.
OPERATIONS = [
    (r"cmr\.earthdata\.nasa\.gov/search/granules", "NASA", "CMR granules"),
    (r"eosdis\.nasa\.gov|earthdata\.nasa\.gov", "NASA", "LANCE fichier"),
    (r"stac\.dataspace\.copernicus\.eu/v1/search", "Copernicus", "STAC search"),
    (r"stac\.dataspace\.copernicus\.eu/v1/collections/([^/]+)", "Copernicus", "STAC {1}"),
    (r"eodata-s3keysmanager\.dataspace", "Copernicus", "clés S3 CDSE"),
    (r"eodata\.dataspace\.copernicus\.eu", "Copernicus", "S3 eodata"),
    (r"identity\.dataspace\.copernicus\.eu", "Copernicus", "jeton CDSE"),
    (r"sh\.dataspace\.copernicus\.eu", "Copernicus", "Sentinel Hub"),
    (r"sentinels\.copernicus\.eu/.*acquisition-plans", "ESA", "plans d'acquisition"),
    (r"sentinels\.copernicus\.eu", "ESA", "fichier de plan"),
    (r"api\.scaleway\.com/.*(?<![\w-])(local-images|private_nics|user_data|action|snapshots|volumes|images|ips|servers)\b",
     "Scaleway", "Scaleway {1}"),
    (r"api\.scaleway\.com", "Scaleway", "Scaleway API"),
    (r"r2\.cloudflarestorage\.com/[^/]+/vignettes/", "R2", "R2 preuve"),
    (r"r2\.cloudflarestorage\.com", "R2", "R2 objet"),
    (r"data\.opensanctions\.org", "OpenSanctions", "OpenSanctions maritime"),
    (r"api\.github\.com/.*/contents/Vessels1\.db", "GitHub", "GitHub version de Vessels1.db"),
    (r"githubusercontent\.com/.*/Vessels1\.db", "GitHub", "GitHub Vessels1.db"),
    (r"vesselfinder\.(com|net)/vessels/details", "VesselFinder", "VesselFinder fiche"),
    (r"vesselfinder\.(com|net)", "VesselFinder", "VesselFinder photo"),
    (r"ntfy", "ntfy", "ntfy alerte"),
    (r"emodnet-humanactivities\.eu", "EMODnet", "EMODnet infrastructures"),
    (r"naciscdn\.org|ngdc\.noaa\.gov|soest\.hawaii\.edu", None, "trait de côte"),
]


def nom_operation(methode: str, url: str, corps: bytes | str | None = None) -> tuple[str | None, str | None]:
    """(service distant, nom du span) d'un appel HTTP sortant : « GET CMR granules VJ102DNB_NRT », « POST STAC
    sentinel-1-grd », « POST Scaleway action ». (None, None) pour un hôte inconnu (appel interne : API, inférence)."""
    p = urlsplit(url)
    hote_chemin = (p.hostname or "") + p.path
    service = service_externe(p.hostname)
    for motif, svc, libelle in OPERATIONS:
        m = re.search(motif, hote_chemin)
        if not m:
            continue
        svc = svc or service
        nom = libelle.replace("{1}", m.group(1) if m.groups() else "")
        if libelle == "CMR granules":                    # produit demandé, en clair dans la requête
            q = re.search(r"(?:^|&)short_name=([A-Za-z0-9_]+)", p.query)
            nom += f" {q.group(1)}" if q else ""
        elif libelle == "STAC search" and corps:         # collection demandée, dans le corps JSON
            try:
                import json
                c = json.loads(corps).get("collections") or []
                nom = f"STAC {c[0]}" if c else nom
            except (ValueError, AttributeError, TypeError):
                pass
        return svc, f"{methode.upper()} {nom}"
    if service:
        return service, f"{methode.upper()} {service}"
    return None, None

# Hôtes dont le chemin est lui même sensible : sujet ntfy (secret), MMSI dans l'adresse de VesselFinder
CHEMIN_MASQUE = ("ntfy.sh", "vesselfinder.com", "vesselfinder.net")

CLE_SENSIBLE = re.compile(r"(?i)(authorization|cookie|token|jeton|secret|password|passwd|mot_de_passe|api_?key|"
                          r"access_?key|credential|signature|ntfy_sujet|sujet)")
URL = re.compile(r"(?i)\b(?:https?|wss?)://[^\s\"'<>]+")
CHEMIN_REQUETE = re.compile(r"^(/[^\s?#]*)[?#]\S*$")
BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
PARAMETRE = re.compile(r"(?i)\b(x-amz-[a-z-]+|awsaccesskeyid|signature|token|jeton|api_?key|apikey|key|sig)=[^&\s\"']+")
CLE_AWS = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")
ATTRIBUTS_RETIRES = {"url.query", "http.request.header.authorization", "http.request.header.cookie"}


def version() -> str:
    """Empreinte courte du commit de l'image (MARS_VERSION, posée à la construction), « dev » hors image."""
    return os.environ.get("MARS_VERSION") or "dev"


def service_externe(host: str | None) -> str | None:
    host = (host or "").lower().split(":")[0]
    for suffix, name in EXTERNES.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    ntfy = urlsplit(os.environ.get("NTFY_URL") or "").hostname
    return "ntfy" if ntfy and host == ntfy else None


def nettoyer_url(url: str) -> str:
    """Adresse sans identifiants, sans chaîne de requête (adresses signées) ni fragment ; chemin masqué pour les hôtes
    où il est sensible (sujet ntfy, MMSI)."""
    try:
        p = urlsplit(url)
    except ValueError:
        return "(adresse illisible)"
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    path = p.path
    ntfy = urlsplit(os.environ.get("NTFY_URL") or "").hostname
    if any(host.split(":")[0].endswith(h) for h in CHEMIN_MASQUE) or (ntfy and host.split(":")[0] == ntfy):
        path = "/…" if path not in ("", "/") else path
    return urlunsplit((p.scheme, host, path, "", ""))


def nettoyer_valeur(v):
    if isinstance(v, str):
        v = URL.sub(lambda m: nettoyer_url(m.group(0)), v)
        v = CHEMIN_REQUETE.sub(r"\1", v)
        v = BEARER.sub(r"\1 ***", v)
        v = PARAMETRE.sub(r"\1=***", v)
        return CLE_AWS.sub("***", v)
    if isinstance(v, (list, tuple)):
        return type(v)(nettoyer_valeur(x) for x in v)
    return v


def propres(attrs) -> dict:
    """Attributs sans donnée sensible : clés sensibles retirées, valeurs nettoyées."""
    out = {}
    for k, v in (attrs or {}).items():
        if v is None or k in ATTRIBUTS_RETIRES or k.startswith("http.request.header.") or CLE_SENSIBLE.search(k):
            continue
        out[k] = nettoyer_valeur(v)
    return out


def _span_propre(s):
    from opentelemetry.sdk.trace import Event, ReadableSpan
    status = s.status
    if status.description:
        status = Status(status.status_code, nettoyer_valeur(status.description))
    return ReadableSpan(name=nettoyer_valeur(s.name), context=s.context, parent=s.parent, resource=s.resource,
                        attributes=propres(s.attributes),
                        events=[Event(e.name, propres(e.attributes), e.timestamp) for e in s.events],
                        links=s.links, kind=s.kind, status=status, start_time=s.start_time, end_time=s.end_time,
                        instrumentation_scope=s.instrumentation_scope)


try:
    from opentelemetry.sdk.trace.export import SpanExporter as _Base
except ImportError:                                     # pragma: no cover
    _Base = object


class Nettoyeur(_Base):
    """Exportateur qui nettoie chaque span avant de le confier au suivant (OTLP, ou mémoire dans les tests)."""

    def __init__(self, suivant):
        self.suivant = suivant

    def export(self, spans):
        return self.suivant.export([_span_propre(s) for s in spans])

    def shutdown(self):
        return self.suivant.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self.suivant.force_flush(timeout_millis)


def init(service: str, exportateur=None):
    """Fournisseur de traces du processus (une seule fois). `exportateur` : exportateur supplémentaire (tests), envoyé
    sans attente."""
    global _provider
    if _provider is not None:
        if exportateur is not None:
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            _provider.add_span_processor(SimpleSpanProcessor(Nettoyeur(exportateur)))
        return _provider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    resource = Resource.create({"service.name": service, "service.namespace": "mars-c2",
                                "service.version": version(),
                                "deployment.environment.name": os.environ.get("MARS_ENVIRONNEMENT", "serveur")})
    _provider = TracerProvider(resource=resource)
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint and os.environ.get("OTEL_SDK_DISABLED", "").lower() != "true":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        _provider.add_span_processor(BatchSpanProcessor(
            Nettoyeur(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces", timeout=5)),
            max_queue_size=8192, schedule_delay_millis=2000))
    if exportateur is not None:
        _provider.add_span_processor(SimpleSpanProcessor(Nettoyeur(exportateur)))
    trace.set_tracer_provider(_provider)
    _instrumenter_requests()
    journaux()
    return _provider


def fermer():
    """Envoie les spans en attente (fin d'une commande)."""
    if _provider is not None:
        _provider.force_flush(5000)


def _instrumenter_requests():
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
    except ImportError:
        return
    if not RequestsInstrumentor().is_instrumented_by_opentelemetry:
        RequestsInstrumentor().instrument(request_hook=requete_sortante)


def requete_sortante(span, request):
    """Appel HTTP sortant (requests) : service distant dans peer.service, nom lisible de l'opération."""
    if span is None or not span.is_recording():
        return
    _nommer(span, getattr(request, "method", None) or "HTTP", str(getattr(request, "url", "") or ""),
            getattr(request, "body", None))


def _nommer(span, methode: str, url: str, corps=None):
    service, nom = nom_operation(methode, url, corps)
    if service:
        span.set_attribute("peer.service", service)
    if nom:
        span.update_name(nom)


# API : routes non tracées (expressions régulières, séparées par des virgules) : flux SSE envoyé chaque seconde,
# contrôle de santé, et vérification du jeton d'un travailleur par nginx avant chaque lot de spans
API_NON_TRACEES = r"api/stream,api/health,api/travailleurs/[0-9]+/otel"


def requete_httpx(span, info):
    """Appel HTTP sortant de l'API (httpx) : service externe nommé (VesselFinder, R2, inférence)."""
    if span is None or not span.is_recording():
        return
    method = info.method.decode() if isinstance(info.method, bytes) else str(info.method)
    _nommer(span, method, str(info.url))


async def requete_httpx_async(span, info):
    requete_httpx(span, info)


# Spans

@contextmanager
def span(nom: str, parent: str | None = None, nouvelle_trace: bool = False, liens: list | None = None, **attributs):
    """Span métier courant. `parent` : contexte W3C (traceparent) d'une trace commencée ailleurs (travailleur) ;
    `nouvelle_trace` : racine d'une trace propre (chaque tâche) ; `liens` : traces apparentées (traceparent W3C, ou
    identifiant de trace seul), reliées par des liens de spans (lot T1 : alerte, demande d'observation, analyse)."""
    from opentelemetry.context import Context
    ctx = contexte(parent) if parent else (Context() if nouvelle_trace else None)
    links = [x for x in (lien(v) for v in (liens or [])) if x is not None]
    with _tracer.start_as_current_span(nom, context=ctx, links=links, attributes=propres(attributs)) as s:
        yield s


def lien(ref: str | None):
    """Lien de span vers une autre trace : traceparent W3C complet, ou identifiant de trace seul (une alerte ne garde
    que celui de sa trace de création) ; le span visé est alors repéré par les 16 premiers chiffres de l'identifiant,
    Tempo ouvre la trace. None si la référence est vide ou invalide."""
    from opentelemetry.trace import Link, SpanContext, TraceFlags
    m = re.fullmatch(r"(?:00-)?([0-9a-f]{32})(?:-([0-9a-f]{16})-[0-9a-f]{2})?", (ref or "").strip())
    if not m or int(m.group(1), 16) == 0:
        return None
    span_id = int(m.group(2) or m.group(1)[:16], 16) or 1
    return Link(SpanContext(int(m.group(1), 16), span_id, is_remote=True, trace_flags=TraceFlags(1)))


def attributs(**kw):
    """Attributs ajoutés au span courant (valeurs nettoyées, None ignoré)."""
    s = trace.get_current_span()
    if s.is_recording():
        s.set_attributes(propres(kw))


def erreur(motif: str):
    """Marque le span courant en erreur sans exception (refus, échec déclaré par un travailleur)."""
    s = trace.get_current_span()
    if s.is_recording():
        s.set_status(Status(StatusCode.ERROR, nettoyer_valeur(motif)[:300]))


def trace_id() -> str | None:
    c = trace.get_current_span().get_span_context()
    return format(c.trace_id, "032x") if c.is_valid else None


def traceparent() -> str | None:
    """Contexte W3C du span courant, à transmettre à un autre processus (travailleur, interface)."""
    c = trace.get_current_span().get_span_context()
    return f"00-{c.trace_id:032x}-{c.span_id:016x}-01" if c.is_valid else None


def contexte(tp: str | None):
    """Contexte OpenTelemetry d'un traceparent W3C (None ou invalide : contexte courant)."""
    if not tp:
        return otel_context.get_current()
    return TraceContextTextMapPropagator().extract({"traceparent": tp})


def trace_de(tp: str | None) -> str | None:
    """Identifiant de trace d'un traceparent W3C."""
    m = re.fullmatch(r"00-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}", tp or "")
    return m.group(1) if m else None


@contextmanager
def sans_instrumentation():
    """Appels HTTP sans span (sondages répétés) : l'instrumentation de requests et de httpx est suspendue le temps du
    bloc ; le span d'attente qui les englobe les résume (sondage)."""
    try:
        from opentelemetry.instrumentation.utils import suppress_instrumentation
    except ImportError:                                     # pragma: no cover
        yield
        return
    with suppress_instrumentation():
        yield


class _Sondage:
    def __init__(self, span):
        self.span, self.n, self.debut, self.etats = span, 0, __import__("time").monotonic(), []

    def etat(self, e):
        """Un sondage de plus, avec l'état lu."""
        self.n += 1
        self.etats.append(str(e))


@contextmanager
def sondage(nom: str, **attributs):
    """Attente par sondages (démarrage d'une instance, carte réseau) : un seul span, avec le nombre de sondages, la
    durée, l'état final et le dernier état intermédiaire ; les requêtes de sondage elles mêmes ne sont pas tracées."""
    import time as _t
    with span(nom, **attributs) as sp:
        so = _Sondage(sp)
        try:
            yield so
        finally:
            sp.set_attributes(propres({"sondage.nombre": so.n, "sondage.duree_s": round(_t.monotonic() - so.debut, 1),
                                       "sondage.etat_final": so.etats[-1] if so.etats else None,
                                       "sondage.dernier_intermediaire": so.etats[-2] if len(so.etats) > 1 else None}))


def en_parallele(fonctions: list, simultanes: int) -> list:
    """Exécute des fonctions sans argument dans des fils (au plus `simultanes` à la fois) et rend leurs résultats dans
    l'ordre. Chaque fil reçoit une copie du contexte de l'appelant : les spans qu'il ouvre (appels HTTP compris)
    restent rattachés au span courant. La première erreur est relevée une fois toutes les fonctions terminées."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, simultanes)) as pool:
        futurs = [pool.submit(contextvars.copy_context().run, f) for f in fonctions]
    return [f.result() for f in futurs]


# Base : identifiant de trace donné à la session (alertes, db/init/27_traces.sql)

@contextmanager
def en_base(conn):
    """Session psycopg : mars.trace_id le temps du bloc (le déclencheur des alertes l'écrit dans leurs détails)."""
    tid = trace_id()
    if tid:
        conn.execute("SELECT set_config('mars.trace_id', %s, false)", (tid,))
    try:
        yield
    finally:
        if tid:
            try:
                conn.execute("SELECT set_config('mars.trace_id', '', false)")
            except Exception:
                pass                                    # connexion perdue : la session disparaît avec elle


@asynccontextmanager
async def en_base_async(c):
    """Même chose pour une connexion asyncpg."""
    tid = trace_id()
    if tid:
        await c.execute("SELECT set_config('mars.trace_id', $1, false)", tid)
    try:
        yield
    finally:
        if tid:
            try:
                await c.execute("SELECT set_config('mars.trace_id', '', false)")
            except Exception:
                pass


# Journaux : trace_id et span_id dans chaque ligne écrite pendant un span

def suffixe() -> str:
    c = trace.get_current_span().get_span_context()
    return f" trace_id={c.trace_id:032x} span_id={c.span_id:016x}" if c.is_valid else ""


class _Lignes:
    """Sortie standard dont chaque ligne écrite pendant un span se termine par ses identifiants."""

    def __init__(self, flux):
        self._flux = flux

    def write(self, s):
        if "\n" in s:
            suf = suffixe()
            if suf:
                s = s.replace("\n", suf + "\n")
        return self._flux.write(s)

    def __getattr__(self, name):
        return getattr(self._flux, name)


class _FiltreJournal(logging.Filter):
    def filter(self, record):
        suf = suffixe()
        if suf and not getattr(record, "_mars_trace", False):
            record.msg, record.args, record._mars_trace = record.getMessage() + suf, None, True
        return True


def journaux():
    if not isinstance(sys.stdout, _Lignes):
        sys.stdout = _Lignes(sys.stdout)
    if not isinstance(sys.stderr, _Lignes):
        sys.stderr = _Lignes(sys.stderr)
    filtre = _FiltreJournal()
    for name in (None, "uvicorn", "uvicorn.error", "uvicorn.access", "mars"):
        for h in logging.getLogger(name).handlers:
            if not any(isinstance(f, _FiltreJournal) for f in h.filters):
                h.addFilter(filtre)
