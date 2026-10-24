import { useEffect, useState } from "react";

/** Flux SSE de l'API en mode direct : trafic quand il a changé.
 *  Le trafic reçu est conservé entre deux envois (le flux ne le renvoie que s'il a changé). */
export function useStream(enabled: boolean, region: string | null) {
  const [traffic, setTraffic] = useState<any>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    const es = new EventSource(`/api/stream?direct=1${region ? `&region=${encodeURIComponent(region)}` : ""}`);
    setTraffic(null);
    es.addEventListener("traffic", (e) => {
      const payload = JSON.parse((e as MessageEvent).data);
      if (payload.navires || payload.traffic) setTraffic(payload);
      setConnected(true);
    });
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, [enabled, region]);

  return { traffic, connected };
}
