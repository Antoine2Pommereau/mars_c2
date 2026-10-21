// Préférences de l'opérateur, gardées par navigateur (rubrique « Affichage et démonstration », dernière rubrique des
// Coulisses). Le stockage peut manquer (navigation privée, données bloquées) : valeur par défaut, sans erreur.

function lire(cle: string): string | null {
  try { return window.localStorage.getItem(cle); } catch { return null; }
}
function ecrire(cle: string, v: string): void {
  try { window.localStorage.setItem(cle, v); } catch { /* stockage indisponible */ }
}

const VUES = ["veille", "trafic", "satellites"] as const;
export type VueCarte = (typeof VUES)[number];

export const lireVueDefaut = (): VueCarte => {
  const v = lire("mars.vue.defaut");
  return VUES.includes(v as VueCarte) ? (v as VueCarte) : "veille";
};
export const garderVueDefaut = (v: VueCarte) => ecrire("mars.vue.defaut", v);

export const lireVignettes = () => lire("mars.fil.vignettes") === "1";
export const garderVignettes = (v: boolean) => ecrire("mars.fil.vignettes", v ? "1" : "0");

export const lireRubrique = () => lire("mars.coulisses.rubrique");
export const garderRubrique = (r: string) => ecrire("mars.coulisses.rubrique", r);
