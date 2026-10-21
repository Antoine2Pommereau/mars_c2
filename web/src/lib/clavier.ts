// Triage au clavier (lot U, étape 5). Veille : J et K déplacent le curseur, Entrée ouvre le dossier, ? l'aide.
// Dossier ouvert : F confirmer, C classer puis 1, 2 ou 3 (motif), S suivre, V vérifier (Entrée demande, Échap annule).
// Aucune touche n'agit pendant une saisie, avec une touche de modification, ou quand la recherche est ouverte.

type Evenement = { key: string; metaKey?: boolean; ctrlKey?: boolean; altKey?: boolean; target?: unknown };

const SAISIE = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** Touche de triage, en minuscule (« j », « enter », « ? », « 1 »), ou null si elle ne doit rien faire */
export function toucheLibre(e: Evenement): string | null {
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  const t = e.target as { tagName?: string; isContentEditable?: boolean } | null | undefined;
  if (t && (SAISIE.has(t.tagName ?? "") || t.isContentEditable)) return null;
  if (typeof document !== "undefined" && document.querySelector("[data-recherche]")) return null;
  return e.key.toLowerCase();
}

/** Clé suivante (sens 1) ou précédente (sens -1) dans la liste ; la première au départ ; bornée aux extrémités */
export function deplacer(cles: string[], courant: string | null, sens: 1 | -1): string | null {
  if (!cles.length) return null;
  const i = courant == null ? -1 : cles.indexOf(courant);
  if (i < 0) return sens === 1 ? cles[0] : cles[cles.length - 1];
  return cles[Math.max(0, Math.min(cles.length - 1, i + sens))];
}
