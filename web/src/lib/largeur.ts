// Largeur des panneaux redimensionnables (retours du lot U) : bornée, jamais plus de la moitié de l'écran, gardée
// par navigateur. Le stockage peut manquer (navigation privée, données bloquées) : valeur par défaut, sans erreur.

/** Largeur bornée à [min, max], et à la moitié de l'écran au plus (min l'emporte sur un écran très étroit) */
export function bornerLargeur(l: number, min: number, max: number, ecran: number): number {
  const haut = Math.max(min, Math.min(max, Math.floor(ecran / 2)));
  return Math.round(Math.max(min, Math.min(haut, Number.isFinite(l) ? l : min)));
}

const cle = (nom: string) => `mars.largeur.${nom}`;

export function lireLargeur(nom: string, defaut: number): number {
  try {
    const v = Number(window.localStorage.getItem(cle(nom)));
    return v > 0 ? v : defaut;
  } catch {
    return defaut;
  }
}

export function ecrireLargeur(nom: string, l: number): void {
  try { window.localStorage.setItem(cle(nom), String(Math.round(l))); } catch { /* stockage indisponible */ }
}
