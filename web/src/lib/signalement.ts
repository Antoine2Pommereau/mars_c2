/** Adresse de la fiche de signalement d'un dossier (lot UX2, components/Signalement.tsx), ouverte dans un nouvel
 *  onglet depuis le récit (bouton ou touche P). */
export const adresseSignalement = (cle: string, instant: string, aLInstant?: string | null) =>
  `/signalement?cle=${encodeURIComponent(cle)}&instant=${encodeURIComponent(instant)}`
  + (aLInstant ? `&a_l_instant=${encodeURIComponent(aLInstant)}` : "");
