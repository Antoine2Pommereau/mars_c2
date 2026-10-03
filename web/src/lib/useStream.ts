import { useEffect, useState } from "react";
import type { StreamPayload } from "./types";

/** Flux SSE de l'API : horloge, trafic, progression des analyses, alertes comportementales. */
export function useStream() {
  const [data, setData] = useState<StreamPayload | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const es = new EventSource("/api/stream");
    es.addEventListener("traffic", (e) => {
      setData(JSON.parse((e as MessageEvent).data));
      setConnected(true);
    });
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, []);

  return { data, connected };
}
