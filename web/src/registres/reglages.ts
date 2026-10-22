// Coulisses : rubriques, groupes et index des réglages (docs/ux_refonte.md, section 13). Chaque réglage ou état a une
// seule place, décidée par deux questions : qui il concerne (moi seul, ou toute la plateforme) et de quoi il parle
// (ce que MARS C2 cherche, ce qu'il regarde et à quel prix, comment il fonctionne). Les outils de travail quotidien
// (navires suivis, couches de la carte) sont dans la Veille. Pur, sans dépendance (web/tests/reglages.test.mjs).

export const GROUPES = [
  { id: "moi", rubriques: ["compte", "notifications", "affichage"] },
  { id: "surveillance", rubriques: ["regles", "zones"] },
  { id: "capteurs", rubriques: ["sources", "activite", "budget"] },
  { id: "plateforme", rubriques: ["systeme", "journal"] },
] as const;
export type Rubrique = (typeof GROUPES)[number]["rubriques"][number];
export const RUBRIQUES: Rubrique[] = GROUPES.flatMap((g) => [...g.rubriques]);
export const estRubrique = (r: unknown): r is Rubrique => RUBRIQUES.includes(r as Rubrique);
const RUBRIQUE_DEFAUT: Rubrique = "systeme";

export interface Reglage { rubrique: Rubrique; partie: string | null; libelle: string; mots: string }

export const REGLAGES: Reglage[] = [
  { rubrique: "compte", partie: null, libelle: "Connexion de l'opérateur", mots: "compte mot de passe deconnexion operateur" },
  { rubrique: "notifications", partie: "notif-alertes", libelle: "Alertes notifiées", mots: "gravite types telephone ntfy" },
  { rubrique: "notifications", partie: "notif-systeme", libelle: "Notifications système et vérification satellite", mots: "pannes disque collecte travailleurs" },
  { rubrique: "notifications", partie: "notif-calmes", libelle: "Heures calmes", mots: "nuit silence debut fin reveil" },
  { rubrique: "notifications", partie: null, libelle: "Notification de test", mots: "essai envoyer" },
  { rubrique: "affichage", partie: "aff-carte", libelle: "Vue de carte par défaut", mots: "veille trafic satellites ouverture" },
  { rubrique: "affichage", partie: "aff-carte", libelle: "Vignettes dans le fil", mots: "photo image alertes" },
  { rubrique: "affichage", partie: "aff-demo", libelle: "Masquer les navires", mots: "demonstration pseudonymes masquage" },
  { rubrique: "affichage", partie: "aff-demo", libelle: "Rejeu tel qu'il était", mots: "demonstration historique" },
  { rubrique: "affichage", partie: "aff-demo", libelle: "Scénarios enregistrés", mots: "demonstration enregistrer vue" },
  { rubrique: "regles", partie: "modes", libelle: "Mode d'une règle", mots: "actif observation coupe coupure ais rendez vous identite" },
  { rubrique: "regles", partie: "seuils", libelle: "Seuils des règles", mots: "version config calibration" },
  { rubrique: "regles", partie: "faux", libelle: "Vrais et faux positifs", mots: "decisions confirmees classees mesures" },
  { rubrique: "zones", partie: null, libelle: "Zones de vigilance", mots: "polygone tampon profil tracer retirer" },
  { rubrique: "zones", partie: null, libelle: "Priorité aux navires suivis", mots: "suivis priorite" },
  { rubrique: "sources", partie: "src-donnees", libelle: "Source AIS", mots: "flux messages debit collecte listes sanctions gur opensanctions" },
  { rubrique: "sources", partie: "capteurs", libelle: "Mettre en pause un capteur", mots: "pause reprise sentinel 1 sentinel 2 viirs verification satellite" },
  { rubrique: "sources", partie: "plafonds", libelle: "Scènes par jour", mots: "plafond quota scene sentinel" },
  { rubrique: "activite", partie: "passages", libelle: "Passages à venir", mots: "sentinel prochain passage nuit viirs" },
  { rubrique: "activite", partie: "verifications", libelle: "Vérifications en cours", mots: "demandes observation actives" },
  { rubrique: "activite", partie: "analyses", libelle: "Analyses récentes", mots: "radar nouvelle analyse emprise" },
  { rubrique: "activite", partie: "demande", libelle: "Demander une observation", mots: "latitude longitude rayon point manuelle" },
  { rubrique: "budget", partie: null, libelle: "Dépenses du mois", mots: "budget euros cout travailleurs gpu mensuel" },
  { rubrique: "systeme", partie: null, libelle: "Services et versions", mots: "collecte ingestion disque memoire archivage grafana echecs" },
  { rubrique: "systeme", partie: "echeances", libelle: "Échéances des secrets", mots: "expiration cles jeton copernicus earthdata github scaleway" },
  { rubrique: "journal", partie: null, libelle: "Journal des actions", mots: "historique decisions operateurs panneau qui a fait" },
];

const normaliser = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
  .replace(/[^a-z0-9]+/g, " ").trim();

/** Réglages dont le libellé ou les mots contiennent tous les mots tapés (début de mot) ; libellé d'abord */
export function chercherReglages(q: string): Reglage[] {
  const mots = normaliser(q).split(" ").filter(Boolean);
  if (!mots.length) return [];
  const contient = (t: string) => mots.every((m) => (` ${t}`).includes(` ${m}`));
  const dans = REGLAGES.filter((r) => contient(normaliser(`${r.libelle} ${r.mots}`)));
  return dans.sort((a, b) => Number(contient(normaliser(b.libelle))) - Number(contient(normaliser(a.libelle))));
}

/** Où mène une adresse : rubrique des Coulisses, ou la Veille pour ce qui en est sorti (suivis, couches) */
export type Destination = { espace: "coulisses"; rubrique: Rubrique } | { espace: "veille"; onglet?: "suivis"; couches?: boolean };

// Rubriques d'avant le 21/10/2026, redirigées vers leur nouvelle place
const ANCIENNES: Record<string, Destination> = {
  operateur: { espace: "coulisses", rubrique: "compte" }, capteurs: { espace: "coulisses", rubrique: "sources" },
  satellites: { espace: "coulisses", rubrique: "activite" }, demo: { espace: "coulisses", rubrique: "affichage" },
  suivis: { espace: "veille", onglet: "suivis" }, couches: { espace: "veille", couches: true },
};

/** Rubrique demandée, sinon la dernière consultée, sinon Système ; une ancienne rubrique va à sa nouvelle place */
export function destination(demandee: string | null | undefined, memorisee: string | null | undefined): Destination {
  if (estRubrique(demandee)) return { espace: "coulisses", rubrique: demandee };
  if (demandee && ANCIENNES[demandee]) return ANCIENNES[demandee];
  return { espace: "coulisses", rubrique: estRubrique(memorisee) ? memorisee : RUBRIQUE_DEFAUT };
}
