// État dégradé (lot U, étape 6) : en direct, sans position AIS reçue depuis plus de 15 minutes, la veille est aveugle.
// Les dossiers affichés datent d'avant la dernière position ; la Veille le dit en tête, sans masquer les vues.

const SEUIL_AVEUGLE_MIN = 15;     // même seuil que le rouge de l'indicateur « Flux AIS » (registres/etat.ts)

/** Minutes sans position et heure de la dernière, ou null si la veille voit (ou si l'on n'est pas en direct) */
export function aveugle(dernierePosition: string | null | undefined, now: number, direct: boolean):
  { minutes: number; derniere: string | null } | null {
  if (!direct) return null;
  const t = dernierePosition ? Date.parse(dernierePosition) : NaN;
  if (Number.isNaN(t)) return { minutes: Infinity, derniere: null };
  const minutes = (now - t) / 60_000;
  return minutes > SEUIL_AVEUGLE_MIN ? { minutes, derniere: dernierePosition! } : null;
}
