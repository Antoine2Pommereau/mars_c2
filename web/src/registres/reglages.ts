// Rubriques des Coulisses et index des réglages pour la recherche (retours du lot U). Chaque réglage mène à sa
// rubrique et à sa partie : « heures calmes » ouvre Notifications sur la partie Heures calmes. Pur, sans dépendance
// (testé par web/tests/reglages.test.mjs).

export const RUBRIQUES = ["systeme", "capteurs", "regles", "satellites", "notifications", "zones", "suivis", "demo",
  "couches", "journal", "operateur"] as const;
export type Rubrique = (typeof RUBRIQUES)[number];
export const estRubrique = (r: unknown): r is Rubrique => RUBRIQUES.includes(r as Rubrique);

export interface Reglage { rubrique: Rubrique; partie: string | null; libelle: string; mots: string }

export const REGLAGES: Reglage[] = [
  { rubrique: "systeme", partie: null, libelle: "État de la plateforme", mots: "voyant flux ais ingestion listes disque archivage version" },
  { rubrique: "systeme", partie: "services", libelle: "Services, disque et échecs", mots: "memoire retard grafana trace panne" },
  { rubrique: "capteurs", partie: "capteurs", libelle: "Mettre en pause un capteur", mots: "pause reprise interrupteur sentinel 1 sentinel 2 viirs tip and cue" },
  { rubrique: "capteurs", partie: "plafonds", libelle: "Scènes par jour", mots: "plafond quota scene sentinel" },
  { rubrique: "capteurs", partie: "depenses", libelle: "Budget et dépenses", mots: "budget euros cout travailleur gpu" },
  { rubrique: "regles", partie: "modes", libelle: "Mode d'une règle", mots: "actif observation coupe coupure ais rendez vous identite liste" },
  { rubrique: "regles", partie: "seuils", libelle: "Seuils des règles", mots: "config rules yaml seuil calibration" },
  { rubrique: "regles", partie: "faux", libelle: "Vrais et faux positifs", mots: "decisions mesures confirmees classees" },
  { rubrique: "satellites", partie: "verifications", libelle: "Vérifications satellites", mots: "tip and cue demandes observation actives" },
  { rubrique: "satellites", partie: "analyses", libelle: "Analyses satellites", mots: "radar sentinel nouvelle analyse emprise" },
  { rubrique: "satellites", partie: "demande", libelle: "Demander une observation", mots: "latitude longitude rayon point" },
  { rubrique: "notifications", partie: "notif-alertes", libelle: "Alertes notifiées", mots: "gravite types ntfy telephone" },
  { rubrique: "notifications", partie: "notif-systeme", libelle: "Notifications système et tip and cue", mots: "pannes disque collecte travailleurs categories" },
  { rubrique: "notifications", partie: "notif-calmes", libelle: "Heures calmes", mots: "nuit silence debut fin reveil" },
  { rubrique: "notifications", partie: null, libelle: "Notification de test", mots: "essai envoyer" },
  { rubrique: "zones", partie: null, libelle: "Zones de vigilance", mots: "polygone tampon profil tracer retirer" },
  { rubrique: "suivis", partie: null, libelle: "Navires suivis", mots: "suivre suivi" },
  { rubrique: "demo", partie: null, libelle: "Masquer les navires", mots: "demonstration pseudonymes masquage" },
  { rubrique: "demo", partie: null, libelle: "Rejeu tel qu'il était", mots: "demonstration scenario enregistrer" },
  { rubrique: "couches", partie: null, libelle: "Couches de la carte", mots: "legende infrastructures cables eoliens couverture reception mouillages" },
  { rubrique: "journal", partie: null, libelle: "Journal des actions", mots: "historique qui a fait" },
  { rubrique: "operateur", partie: null, libelle: "Connexion de l'opérateur", mots: "mot de passe deconnexion compte" },
];

/** Minuscules, sans accents ni ponctuation */
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
