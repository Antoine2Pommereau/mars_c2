// Pages d'attente (503, 502) : interroge /api/health toutes les cinq secondes et revient à la page d'origine
// (paramètre « retour », adresse du site seulement) dès que le service répond.
const demande = new URLSearchParams(location.search).get("retour");
const retour = demande && demande.startsWith("/") && !demande.startsWith("//") ? demande
  : location.pathname.startsWith("/erreurs/") ? "/" : location.pathname + location.search;
setInterval(async () => {
  try {
    const r = await fetch("/api/health", { cache: "no-store" });
    if (r.ok && (r.headers.get("content-type") || "").includes("json")) location.replace(retour);
  } catch { /* toujours injoignable : nouvel essai */ }
}, 5000);
