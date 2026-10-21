// Échelle de couleur unique de l'interface (lot U) : l'urgence, en trois niveaux. Le type d'un événement passe par
// son icône, jamais par sa couleur ; les couleurs par type ne restent que dans la vue « Trafic complet ».
export type Niveau = 1 | 2 | 3;
export const URGENCE: Record<Niveau, string> = { 3: "#ef6461", 2: "#f0a84b", 1: "#aab6bf" };

/** Urgence d'une alerte seule, d'après sa gravité (même base que les dossiers : critique 3, élevée 2, sinon 1) */
export const urgenceGravite = (g?: string): Niveau => (g === "critique" ? 3 : g === "elevee" ? 2 : 1);
