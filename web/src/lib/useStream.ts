import { useEffect, useState } from "react";
import type { StreamPayload } from "./types";

/** Flux SSE de l'API : horloge, trafic, progression des analyses, alertes comportementales.
 *  Se reconnecte seul avec un repli exponentiel borné quand la source se ferme pour de bon. */
export function useStream() {
  const [data, setData] = useState<StreamPayload | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let delay = 1000; // premier repli, puis doublement jusqu'à la borne
    let closed = false; // l'effet a été nettoyé : ne plus rien recréer

    const connect = () => {
      if (closed) return;
      es = new EventSource("/api/stream");
      es.addEventListener("traffic", (e) => {
        setData(JSON.parse((e as MessageEvent).data));
        setConnected(true);
        delay = 1000; // une trame reçue : on réarme le repli
      });
      es.onerror = () => {
        setConnected(false);
        // Coupure non récupérable : le navigateur ne retentera pas, on recrée la source
        if (es && es.readyState === EventSource.CLOSED) {
          es.close();
          es = null;
          if (closed || timer) return;
          timer = setTimeout(() => {
            timer = null;
            delay = Math.min(delay * 2, 15000);
            connect();
          }, delay);
        }
      };
    };

    connect();

    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      es?.close();
    };
  }, []);

  return { data, connected };
}
