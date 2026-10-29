// Symbole de MARS C2, « viseur » (lot UX1, canevas Identité, proposition A) : anneau, quatre repères, flèche de cap.
// Source unique : logotype de l'interface, favicon qui informe (lib/onglet.ts) et script des icônes
// (web/outils/icones/generer.mjs, qui produit les PNG, le manifeste et l'image d'aperçu). Aucune dépendance : le
// script l'importe directement.

export const COULEUR_SYMBOLE = { sombre: "#4fb6c8", clair: "#1f7f91" } as const;
export const COULEUR_PASTILLE = { examiner: "#f0a84b", urgent: "#ef6461" } as const;
export const FOND = { sombre: "#0e1419", clair: "#ffffff", carre: "#141c23" } as const;

const ANNEAU = (epaisseur: number) => `<circle cx="16" cy="16" r="12" fill="none" stroke-width="${epaisseur}"/>`;
const REPERES = '<path d="M16 2.5v3M16 26.5v3M2.5 16h3M26.5 16h3" fill="none" stroke-width="2" stroke-linecap="round"/>';
const FLECHE = '<path d="M16 9.5l4.6 10.2L16 17.2l-4.6 2.5z" stroke="none"/>';
const PASTILLE = '<circle cx="25" cy="7" r="6.5" stroke-width="2"/>';

export type Options = {
  /** Couleur du trait et de la flèche, ou deux couleurs (fond clair, fond sombre) choisies par prefers-color-scheme. */
  couleur: string | { clair: string; sombre: string };
  /** Version 16 px : anneau plus épais, sans les repères (illisibles à cette taille). */
  petit?: boolean;
  /** Couleur de la pastille d'alerte ; son liseré prend la couleur du fond (`lisere`). */
  pastille?: string | null;
  lisere?: string | { clair: string; sombre: string };
  /** Taille de rendu en pixels (attributs width et height) ; sans elle, le SVG suit son conteneur. */
  px?: number;
  /** Symbole posé sur un carré arrondi de cette couleur (icônes d'application), à `marge` de son bord (en unités). */
  carre?: string;
  marge?: number;
  titre?: string;
  /** Inséré dans la page : sans feuille de style (elle s'appliquerait à tout le document), couleur du texte
   *  (currentColor), liseré de la pastille donné par `lisere` (une couleur). */
  enLigne?: boolean;
};

/** Le symbole en SVG autonome (balisage). */
export function symboleSvg(o: Options): string {
  const double = typeof o.couleur !== "string";
  const c = typeof o.couleur === "string" ? { clair: o.couleur, sombre: o.couleur } : o.couleur;
  const l = o.lisere == null ? FOND : typeof o.lisere === "string" ? { clair: o.lisere, sombre: o.lisere } : o.lisere;
  if (o.enLigne) {
    const lis = typeof o.lisere === "string" ? o.lisere : "currentColor";
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"${o.px ? ` width="${o.px}" height="${o.px}"` : ""} ` +
      `aria-hidden="true"><g stroke="currentColor" fill="currentColor">${ANNEAU(o.petit ? 2.6 : 2.2)}${o.petit ? "" : REPERES}` +
      `${FLECHE}</g>${o.pastille ? `<g stroke="${lis}" fill="${o.pastille}">${PASTILLE}</g>` : ""}</svg>`;
  }
  const style = `.t{stroke:${c.clair};fill:${c.clair}}.p{stroke:${l.clair}}` +
    (double || typeof o.lisere === "object"
      ? `@media (prefers-color-scheme: dark){.t{stroke:${c.sombre};fill:${c.sombre}}.p{stroke:${l.sombre}}}` : "");
  const m = o.carre ? (o.marge ?? 7) : 0;
  const vb = `${-m} ${-m} ${32 + 2 * m} ${32 + 2 * m}`;
  const taille = o.px ? ` width="${o.px}" height="${o.px}"` : "";
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${vb}"${taille} role="img">` +
    (o.titre ? `<title>${o.titre}</title>` : "") + `<style>${style}</style>` +
    (o.carre ? `<rect x="${-m}" y="${-m}" width="${32 + 2 * m}" height="${32 + 2 * m}" rx="${(32 + 2 * m) * 0.22}" fill="${o.carre}"/>` : "") +
    `<g class="t">${ANNEAU(o.petit ? 2.6 : 2.2)}${o.petit ? "" : REPERES}${FLECHE}</g>` +
    (o.pastille ? `<g class="p" fill="${o.pastille}">${PASTILLE}</g>` : "") + "</svg>";
}

/** Adresse data: d'un SVG (favicon dynamique). */
export const dataSvg = (svg: string) => `data:image/svg+xml,${encodeURIComponent(svg)}`;
