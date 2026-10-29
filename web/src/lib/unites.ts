// Unités marines (lot UX1) : coordonnées en degrés et minutes décimales (degrés décimaux en option), distances en
// milles nautiques (mètres sous 0,1 mille pour les preuves fines), vitesses en nœuds, heures UTC suffixées Z (groupe
// date heure en option pour les dossiers et les alertes), heure de Paris au survol. Réglages gardés par navigateur
// (Coulisses, Affichage) ; un changement recharge la page, comme le masquage de démonstration.

export type ModeCoord = "dm" | "dd";
export type ModeHorodatage = "utc" | "gdh";

const CLES = { coord: "mars.unites.coord", horodatage: "mars.unites.horodatage" };
const lire = (cle: string) => { try { return window.localStorage.getItem(cle); } catch { return null; } };

export const reglagesUnites = {
  coord: (lire(CLES.coord) === "dd" ? "dd" : "dm") as ModeCoord,
  horodatage: (lire(CLES.horodatage) === "gdh" ? "gdh" : "utc") as ModeHorodatage,
};

export function choisirUnites(r: Partial<typeof reglagesUnites>) {
  try {
    if (r.coord) window.localStorage.setItem(CLES.coord, r.coord);
    if (r.horodatage) window.localStorage.setItem(CLES.horodatage, r.horodatage);
  } catch { /* stockage indisponible */ }
  window.location.reload();
}

const fr = (v: number, d: number) => v.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
const pad = (n: number) => String(n).padStart(2, "0");

/** « 48°38,7′ N 4°12,3′ O » (latitude d'abord), ou « 48,6450° N 4,2050° O » en degrés décimaux. */
export function coord(lon: number, lat: number, mode: ModeCoord = reglagesUnites.coord): string {
  const un = (v: number, pos: string, neg: string) => {
    const h = v < 0 ? neg : pos, a = Math.abs(v);
    if (mode === "dd") return `${fr(a, 4)}° ${h}`;
    let d = Math.floor(a), m = Math.round((a - d) * 600) / 10;
    if (m >= 60) { d += 1; m = 0; }
    return `${d}°${m < 10 ? "0" : ""}${fr(m, 1)}′ ${h}`;              // minutes sur deux chiffres : 51°09,4′
  };
  return `${un(lat, "N", "S")} ${un(lon, "E", "O")}`;
}

const MILLE_M = 1852;

/** Distance en milles nautiques : deux décimales sous 1 mille, une au delà ; `fine` (appariement radar, preuve
 *  rapprochée) : en mètres sous 0,1 mille. */
export function distance(m: number | null | undefined, fine = false): string {
  if (m == null || Number.isNaN(Number(m))) return "n.d.";
  const nm = Number(m) / MILLE_M;
  if (fine && nm < 0.1) return `${Math.round(Number(m))} m`;
  return `${fr(nm, nm < 1 ? 2 : 1)} NM`;
}
export const distanceKm = (km: number | null | undefined, fine = false) => distance(km == null ? km : Number(km) * 1000, fine);

export const vitesse = (kn: number | null | undefined) => (kn == null ? "n.d." : `${fr(Number(kn), 1)} nœuds`);

const MOIS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

/** Groupe date heure : « 291654Z OCT 26 » (jour, heure et minute UTC, mois, année). */
export function gdh(iso: string | number | null | undefined): string {
  if (iso == null) return "n.d.";
  const d = new Date(iso);
  return `${pad(d.getUTCDate())}${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}Z ${MOIS[d.getUTCMonth()]} ${pad(d.getUTCFullYear() % 100)}`;
}

/** Heure de Paris d'un instant, pour l'info bulle : « 29/10/2026 17:54 heure de Paris ». */
export function heureParis(iso: string | number | null | undefined): string {
  if (iso == null) return "";
  const p = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit" }).format(new Date(iso)).replace(",", "");
  return `${p} heure de Paris`;
}

/** Texte libre venu de l'API (contexte d'une alerte, motif) : distances « 6,2 km » et « 487 m » converties en milles
 *  (mètres gardés sous 0,1 mille, preuve fine). Les surfaces (km²) et les tailles de navire (« 52 m » suivi d'un mot
 *  de longueur) ne sont pas des distances : elles restent. */
export function texteMarin(s: string): string {
  return s
    .replace(/(\d+(?:[.,]\d+)?)\s?km(?![²2\w])/g, (_, v) => distance(Number(v.replace(",", ".")) * 1000))
    .replace(/(^|[^\p{L}])(à moins de|à)\s(\d+(?:[.,]\d+)?)\s?m(?![\p{L}²2])/giu,
      (_, avant, prep, v) => `${avant}${prep} ${distance(Number(v.replace(",", ".")), true)}`);
}
