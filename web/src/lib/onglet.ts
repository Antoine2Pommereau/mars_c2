// Onglet du navigateur (lot UX1) : titre « (N) MARS C2 · objet » et favicon qui informe. N et la pastille viennent
// des dossiers de la Veille déjà chargés (aucune requête de plus) : N = urgents (niveau 3) plus à examiner (niveau 2),
// pastille rouge s'il y a au moins un urgent, orange sinon. Rien en mode démonstration (ni compte ni pastille).
import { COULEUR_PASTILLE, COULEUR_SYMBOLE, dataSvg, symboleSvg } from "./symbole.ts";

export type Pastille = "urgent" | "examiner" | null;
export type EtatOnglet = { titre: string; pastille: Pastille; compte: number };

const NOM = "MARS C2";

export function etatOnglet(dossiers: { niveau: number }[] | undefined, objet: string, demo: boolean): EtatOnglet {
  const urgents = demo ? 0 : (dossiers ?? []).filter((d) => d.niveau >= 3).length;
  const compte = demo ? 0 : (dossiers ?? []).filter((d) => d.niveau >= 2).length;
  const pastille: Pastille = urgents ? "urgent" : compte ? "examiner" : null;
  return { titre: `${compte ? `(${compte}) ` : ""}${NOM} · ${objet}`, pastille, compte };
}

/** Favicon de l'onglet : symbole 16 px, pastille selon l'état ; la couleur suit le thème du système (barre d'onglets). */
export const faviconOnglet = (p: Pastille) =>
  p ? dataSvg(symboleSvg({ couleur: COULEUR_SYMBOLE, petit: true, pastille: COULEUR_PASTILLE[p] })) : "/favicon.svg";

/** Pose le titre et le favicon (navigateur seulement), sans rien toucher s'ils n'ont pas changé. */
export function appliquerOnglet(e: EtatOnglet) {
  if (typeof document === "undefined") return;
  if (document.title !== e.titre) document.title = e.titre;
  const lien = document.querySelector<HTMLLinkElement>('link[rel="icon"][type="image/svg+xml"]');
  const href = faviconOnglet(e.pastille);
  if (lien && lien.getAttribute("href") !== href) lien.setAttribute("href", href);
}
