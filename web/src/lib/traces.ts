// Traces OpenTelemetry de l'interface (étape D2) : chaque requête vers l'API est un span, dont le contexte part avec
// la requête (en tête traceparent) ; l'API rejoint ainsi la même trace. Envoi par lots au collecteur, par nginx
// (/otel/v1/traces), en OTLP JSON. Aucune donnée de la carte : méthode, chemin réduit à son gabarit (identifiants
// remplacés par {id}, chaîne de requête retirée : ni recherche, ni position, ni nom), code de réponse, durée.
// Sans collecteur (Mac, développement), le premier envoi en échec suspend les envois dix minutes.

declare const __MARS_VERSION__: string;
/** Empreinte du commit de l'image de l'interface (posée à la construction), « dev » en développement */
export const VERSION_INTERFACE: string = typeof __MARS_VERSION__ === "string" ? __MARS_VERSION__ : "dev";

interface SpanOtlp { traceId: string; spanId: string; name: string; kind: number; startTimeUnixNano: string;
  endTimeUnixNano: string; attributes: { key: string; value: { stringValue?: string; intValue?: string } }[];
  status: { code: number; message?: string } }

const enAttente: SpanOtlp[] = [];
let suspenduJusqua = 0;
const hex = (n: number) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, "0")).join("");
const nanos = (ms: number) => `${Math.round(ms * 1e6)}`;
/** Chemin sans chaîne de requête, identifiants numériques remplacés : /api/vessels/{id}/track */
const gabarit = (url: string) => url.split(/[?#]/)[0].replace(/\/\d+(?=\/|$)/g, "/{id}");

async function envoyer() {
  if (!enAttente.length || Date.now() < suspenduJusqua) return;
  const lot = enAttente.splice(0, 200);
  const corps = { resourceSpans: [{
    resource: { attributes: [{ key: "service.name", value: { stringValue: "mars-web" } },
      { key: "service.namespace", value: { stringValue: "mars-c2" } },
      { key: "service.version", value: { stringValue: VERSION_INTERFACE } }] },
    scopeSpans: [{ scope: { name: "mars.web" }, spans: lot }] }] };
  try {
    const r = await fetch("/otel/v1/traces", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corps), keepalive: true });
    if (!r.ok) suspenduJusqua = Date.now() + 10 * 60_000;
  } catch {
    suspenduJusqua = Date.now() + 10 * 60_000;
  }
}
setInterval(envoyer, 10_000);

/** fetch tracé : un span client par requête vers l'API, contexte W3C transmis */
export async function appel(url: string, init: RequestInit = {}): Promise<Response> {
  const traceId = hex(16), spanId = hex(8), methode = (init.method ?? "GET").toUpperCase();
  const debut = performance.timeOrigin + performance.now();
  const headers = new Headers(init.headers);
  headers.set("traceparent", `00-${traceId}-${spanId}-01`);
  const span = (code: number | null, erreur?: string) => enAttente.length < 1000 && enAttente.push({
    traceId, spanId, name: `${methode} ${gabarit(url)}`, kind: 3, startTimeUnixNano: nanos(debut),
    endTimeUnixNano: nanos(performance.timeOrigin + performance.now()),
    attributes: [{ key: "http.request.method", value: { stringValue: methode } },
      { key: "url.path", value: { stringValue: gabarit(url) } },
      ...(code != null ? [{ key: "http.response.status_code", value: { intValue: String(code) } }] : [])],
    status: erreur ? { code: 2, message: erreur } : { code: 1 } });
  try {
    const r = await fetch(url, { ...init, headers });
    span(r.status, r.status >= 500 ? `HTTP ${r.status}` : undefined);
    return r;
  } catch (e) {
    span(null, e instanceof Error ? e.name : "erreur réseau");
    throw e;
  }
}
