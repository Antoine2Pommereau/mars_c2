/** Connexion à la demande (lot Z1) : toute écriture de l'API exige une session d'opérateur. Une écriture refusée
 *  faute de session (401) ouvre la fenêtre de connexion (components/ConnexionDemandee.tsx) ; une fois connecté,
 *  l'écriture reprend d'elle même. Sans fenêtre montée, ou si l'opérateur renonce, l'erreur remonte telle quelle. */
type Demandeur = () => Promise<boolean>;
let demandeur: Demandeur | null = null;

export function enregistrerDemandeur(d: Demandeur | null) {
  demandeur = d;
}

export function demanderConnexion(): Promise<boolean> {
  return demandeur ? demandeur() : Promise.resolve(false);
}
