// Mise à jour du serveur (lot UX1) : pendant un déploiement, nginx répond aux appels de l'API par sa page « Mise à
// jour en cours » (503, HTML ; l'API ne renvoie jamais de HTML). Deux réponses de suite ainsi : l'interface bascule
// sur cette page, qui revient seule à l'adresse d'origine dès que le service répond (web/public/erreurs/retour.js).

let suite = 0;
const SEUIL = 2;

/** Vrai quand la réponse est la page de mise à jour de nginx. */
export const pageDeMaj = (status: number, type: string | null) => (status === 502 || status === 503) && (type ?? "").includes("text/html");

/** À appeler pour chaque réponse d'une lecture de l'API ; retourne vrai au moment de basculer. */
export function signalerReponse(status: number, type: string | null): boolean {
  suite = pageDeMaj(status, type) ? suite + 1 : 0;
  return suite >= SEUIL;
}

export function basculerSurMaj() {
  const retour = window.location.pathname + window.location.search;
  window.location.assign(`/erreurs/503.html?retour=${encodeURIComponent(retour)}`);
}
