import { DEFAUT, type Duree, type Mode, type Temps } from "./temps";

// État de l'écran dans l'adresse de la page : un lien rouvre exactement la même vue.
// ?mode=plage&debut=…&fin=…&instant=…&vitesse=60&sel=alerte:12&couches=navires,eoliens&zone=bretagne

interface EtatEcran {
  temps: Temps;
  sel: string | null;          // « alerte:12 », « navire:345 », « detection:7 »
  couches: string[] | null;    // null : couches par défaut
  zone: string | null;
}

const ms = (v: string | null) => (v ? Date.parse(v) : undefined);

export function lireAdresse(search = window.location.search): EtatEcran {
  const q = new URLSearchParams(search);
  const mode = (q.get("mode") as Mode) || DEFAUT.mode;
  const temps: Temps = {
    ...DEFAUT, mode: ["direct", "plage", "rejeu"].includes(mode) ? mode : "direct",
    duree: (q.get("duree") as Duree) || DEFAUT.duree,
    debut: ms(q.get("debut")), fin: ms(q.get("fin")), instant: ms(q.get("instant")),
    vitesse: Number(q.get("vitesse")) || DEFAUT.vitesse, lecture: false,
  };
  if (temps.mode !== "direct" && (temps.debut == null || temps.fin == null || Number.isNaN(temps.debut))) temps.mode = "direct";
  return { temps, sel: q.get("sel"), couches: q.has("couches") ? q.get("couches")!.split(",").filter(Boolean) : null, zone: q.get("zone") };
}

export function ecrireAdresse(e: EtatEcran) {
  const q = new URLSearchParams();
  q.set("mode", e.temps.mode);
  q.set("duree", e.temps.duree);
  if (e.temps.mode !== "direct") {
    if (e.temps.debut != null) q.set("debut", new Date(e.temps.debut).toISOString());
    if (e.temps.fin != null) q.set("fin", new Date(e.temps.fin).toISOString());
    if (e.temps.instant != null) q.set("instant", new Date(e.temps.instant).toISOString());
    q.set("vitesse", String(e.temps.vitesse));
  }
  if (e.sel) q.set("sel", e.sel);
  if (e.couches) q.set("couches", e.couches.join(","));
  if (e.zone) q.set("zone", e.zone);
  const next = `${window.location.pathname}?${q.toString()}`;
  if (next !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, "", next);
}
