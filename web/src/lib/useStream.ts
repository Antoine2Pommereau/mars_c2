import { useEffect, useState } from "react";
import type { StreamPayload } from "./types";

/** Flux SSE de l'API en mode direct : horloge chaque seconde, trafic quand il a changé, progression des analyses.
 *  Le trafic reçu est conservé entre deux envois (le flux ne le renvoie que s'il a changé). */
export function useStream(enabled: boolean) {
  const [data, setData] = useState<StreamPayload | null>(null);
  const [traffic, setTraffic] = useState<any>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    const es = new EventSource("/api/stream?direct=1");
    es.addEventListener("traffic", (e) => {
      const payload = JSON.parse((e as MessageEvent).data);
      setData(payload);
      if (payload.navires || payload.traffic) setTraffic(payload);
      setConnected(true);
    });
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, [enabled]);

  return { data, traffic, connected };
}
