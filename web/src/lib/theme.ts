// Thème de l'interface : sombre (design validé, par défaut), clair, ou celui du système. Gardé par navigateur
// (rubrique « Affichage et démonstration ») ; posé sur <html data-theme> avant le premier rendu, les jetons de
// styles.css font le reste. La carte (MapLibre) ne lit pas les variables CSS : ses couleurs, écrites pour le thème
// sombre, sont teintes par `teinter` (table TEINTES_CLAIR) et son fond change de style (FOND_CARTE).
import { useSyncExternalStore } from "react";

export type Theme = "sombre" | "clair";
export type Preference = Theme | "systeme";
export const PREFERENCES: Preference[] = ["sombre", "clair", "systeme"];

const CLE = "mars.theme";
const SYSTEME = "(prefers-color-scheme: light)";

function lire(): Preference {
  try {
    const v = window.localStorage.getItem(CLE);
    return PREFERENCES.includes(v as Preference) ? (v as Preference) : "sombre";
  } catch { return "sombre"; }
}

const resoudre = (p: Preference): Theme =>
  p !== "systeme" ? p : typeof window !== "undefined" && window.matchMedia?.(SYSTEME).matches ? "clair" : "sombre";

let etat = { preference: lire(), theme: resoudre(lire()) };
const abonnes = new Set<() => void>();

function appliquer() {
  const el = document.documentElement;
  el.dataset.theme = etat.theme === "clair" ? "light" : "dark";
  el.style.colorScheme = etat.theme === "clair" ? "light" : "dark";
}

function publier(preference: Preference) {
  const theme = resoudre(preference);
  if (preference === etat.preference && theme === etat.theme) return;
  etat = { preference, theme };
  appliquer();
  abonnes.forEach((f) => f());
}

export function choisirTheme(p: Preference) {
  try { window.localStorage.setItem(CLE, p); } catch { /* stockage indisponible */ }
  publier(p);
}

/** À appeler une fois, avant le premier rendu : thème posé sans éclair sombre, suivi du système si demandé. */
export function initialiserTheme() {
  appliquer();
  window.matchMedia?.(SYSTEME).addEventListener?.("change", () => { if (etat.preference === "systeme") publier("systeme"); });
}

export function useTheme() {
  return useSyncExternalStore((f) => { abonnes.add(f); return () => abonnes.delete(f); }, () => etat);
}

// Carte

export const FOND_CARTE: Record<Theme, string> = {
  sombre: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  clair: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
};

/** Couleurs de la carte écrites pour le thème sombre et leur équivalent sur le fond clair (Positron) : l'encre et
 *  les gris s'inversent, les teintes trop pâles (jaune, pastels des types de navire) s'assombrissent ; les couleurs
 *  d'alerte franches (rouge, orange, rose, violet) restent les mêmes. */
export const TEINTES_CLAIR: Record<string, string> = {
  "#e6ecf0": "#1d2933",  // encre
  "#0e1419": "#ffffff",  // halo des textes, contour des points
  "#7c8b97": "#5b6b78",  // secondaire
  "#4c5a66": "#9aa6b0",  // discret (écartées)
  "#55636f": "#a3afb9",  // poussière du trafic en Veille
  "#c9d3da": "#4f5d69",  // navire neutre
  "#c2ccd3": "#56636e",  // zones de vigilance
  "#4fb6c8": "#1f8798",  // signal
  "#aab6bf": "#6f7c86",  // urgence 1
  "#9fb3c8": "#5d7489",  // passages satellites
  "#9fb3c2": "#6b7f8e",  // type de navire inconnu
  "#6ea8fe": "#2f6fd6",  // cargo
  "#f0a35e": "#cf6f1c",  // pétrolier
  "#5fd38d": "#22955a",  // pêche
  "#c792ea": "#8e4fc0",  // passagers
  "#f5e663": "#a68f00",  // plaisance
  "#e07a5f": "#c4553a",  // service
  "#e8d45a": "#9c8700",  // position non confirmée
  "#5fd3a5": "#1f9a6e",  // changement d'identité
  "#ffffff": "#1d2933",
  "rgba(255,255,255,0.04)": "rgba(0,0,0,0.04)",
};

/** Valeur (couleur, expression MapLibre, objet de peinture) teinte pour le thème ; inchangée en sombre. Seules les
 *  chaînes égales à une couleur de la table changent : les clés d'un « match » ne sont jamais des couleurs ici. */
export function teinter<T>(v: T, theme: Theme): T {
  if (theme === "sombre") return v;
  if (typeof v === "string") return (TEINTES_CLAIR[v.toLowerCase()] ?? v) as T;
  if (Array.isArray(v)) return v.map((x) => teinter(x, theme)) as T;
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, teinter(x, theme)])) as T;
  return v;
}
