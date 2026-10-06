import { L } from "./libelles";

// Auteur des décisions, commentaires et notes : un seul opérateur pour l'instant, mémorisé dans le navigateur.
const CLE = "mars.auteur";

export function auteurMemorise(): string {
  try { return localStorage.getItem(CLE) || L.decisions.auteurDefaut; } catch { return L.decisions.auteurDefaut; }
}

export function memoriserAuteur(a: string) {
  try { localStorage.setItem(CLE, a); } catch { /* stockage indisponible : auteur non mémorisé */ }
}
