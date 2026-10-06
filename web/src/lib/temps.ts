// Le temps de l'écran : une plage (période étudiée) et un instant (position des navires), tenus par l'interface et
// inscrits dans l'adresse de la page. Trois modes : direct (l'instant suit l'heure réelle, la plage glisse), plage
// (période fixe, instant choisi), rejeu (l'instant avance dans la plage à la vitesse choisie).

export type Mode = "direct" | "plage" | "rejeu";
export type Duree = "1h" | "6h" | "24h" | "7j" | "30j" | "libre";

export interface Temps {
  mode: Mode;
  duree: Duree;
  debut?: number;      // millisecondes, plage et rejeu
  fin?: number;
  instant?: number;
  vitesse: number;
  lecture: boolean;
}

const H = 3600_000;
const DUREES: Record<Exclude<Duree, "libre">, number> = { "1h": H, "6h": 6 * H, "24h": 24 * H, "7j": 168 * H, "30j": 720 * H };
export const PRESETS = Object.keys(DUREES) as Exclude<Duree, "libre">[];
export const VITESSES = [1, 10, 60, 300];
const CONSERVATION = 720 * H;    // positions gardées 30 jours en base
export const DEFAUT: Temps = { mode: "direct", duree: "24h", vitesse: 60, lecture: false };

/** Plage et instant effectifs à l'heure `now`. */
export function resolve(t: Temps, now: number): { debut: number; fin: number; instant: number } {
  if (t.mode === "direct" || t.debut == null || t.fin == null) {
    const d = t.duree === "libre" ? DUREES["24h"] : DUREES[t.duree];
    return { debut: now - d, fin: now, instant: now };
  }
  const instant = Math.min(Math.max(t.instant ?? t.fin, t.debut), t.fin);
  return { debut: t.debut, fin: t.fin, instant };
}

/** Une plage ne remonte pas au delà de la conservation en base, ni dans le futur. */
function clamp(debut: number, fin: number, now: number): [number, number] {
  const f = Math.min(fin, now);
  const d = Math.max(Math.min(debut, f - 60_000), now - CONSERVATION);
  return [d, f];
}

/** Pas des graduations de la frise selon la longueur de la plage. */
export function ticks(debut: number, fin: number): number[] {
  const span = fin - debut;
  const steps = [15 * 60_000, H, 3 * H, 6 * H, 12 * H, 24 * H, 48 * H, 7 * 24 * H];
  const step = steps.find((s) => span / s <= 8) ?? 7 * 24 * H;
  const out: number[] = [];
  // Graduations alignées sur l'heure UTC ronde
  for (let t = Math.ceil(debut / step) * step; t <= fin; t += step) out.push(t);
  return out;
}

export const iso = (ms: number) => new Date(ms).toISOString();

// Transitions de mode, depuis la frise

export const versDirect = (t: Temps): Temps =>
  ({ ...t, mode: "direct", duree: t.duree === "libre" ? "24h" : t.duree, debut: undefined, fin: undefined, instant: undefined, lecture: false });

/** Raccourci de durée : en direct la plage glisse ; sinon la fin est gardée et le début recule. */
export function avecDuree(t: Temps, d: Exclude<Duree, "libre">, now: number): Temps {
  if (t.mode === "direct") return { ...t, duree: d };
  const { fin, instant } = resolve(t, now);
  const [debut] = clamp(fin - DUREES[d], fin, now);
  return { ...t, duree: d, debut, fin, instant: Math.max(instant, debut) };
}

/** Plage libre : fixe la période, quitte le direct ; l'instant se place à la fin de la plage. */
export function avecPlage(t: Temps, debut: number, fin: number, now: number): Temps {
  const [d, f] = clamp(debut, fin, now);
  return { ...t, mode: t.mode === "rejeu" ? "rejeu" : "plage", duree: "libre", debut: d, fin: f, instant: f, lecture: false };
}

/** Instant choisi sur la frise : en direct, la plage courante est figée. */
export function avecInstant(t: Temps, instant: number, now: number): Temps {
  const r = resolve(t, now);
  return { ...t, mode: t.mode === "direct" ? "plage" : t.mode, debut: r.debut, fin: r.fin,
           instant: Math.min(Math.max(instant, r.debut), r.fin) };
}

export function lecture(t: Temps, now: number, on: boolean): Temps {
  const r = resolve(t, now);
  const instant = on && r.instant >= r.fin ? r.debut : r.instant;     // relance depuis le début de la plage
  return { ...t, mode: "rejeu", debut: r.debut, fin: r.fin, instant, lecture: on };
}
