import type { Props } from "./types";

// Sources d'un navire des listes regroupées par organisme : GUR, puis chaque autorité des listes d'OpenSanctions
// (reconnue au préfixe du jeu de données), avec les motifs et les liens de toutes ses entrées.

interface Organisme { cle: string; motifs: string[]; liens: string[] }

const PREFIXES: [string, string][] = [
  ["eu_", "ue"], ["gb_", "gb"], ["us_", "us"], ["ca_", "ca"], ["ch_", "ch"], ["au_", "au"], ["nz_", "nz"],
  ["jp_", "jp"], ["ua_", "ua"], ["un_", "onu"], ["tokyo_mou", "tokyo_mou"], ["paris_mou", "paris_mou"],
];
const ORDRE = ["gur", "ue", "gb", "us"];
const MOTIFS: Record<string, string> = { sanction: "sanction", "mare.shadow": "flotte", "mare.detained": "immobilisation", poi: "poi" };

function organisme(e: Props): string {
  if (e.source === "gur") return "gur";
  const ds: string = (e.datasets ?? [])[0] ?? "opensanctions";
  return PREFIXES.find(([p]) => ds.startsWith(p))?.[1] ?? ds;
}

export function grouperSources(entries: Props[]): Organisme[] {
  const parOrg = new Map<string, Organisme>();
  for (const e of entries) {
    const cle = organisme(e);
    const o = parOrg.get(cle) ?? { cle, motifs: [], liens: [] };
    for (const r of e.risks ?? []) {
      const m = MOTIFS[r] ?? r;
      if (!o.motifs.includes(m)) o.motifs.push(m);
    }
    if (e.url && !o.liens.includes(e.url)) o.liens.push(e.url);
    parOrg.set(cle, o);
  }
  const rang = (k: string) => (ORDRE.includes(k) ? ORDRE.indexOf(k) : ORDRE.length);
  return [...parOrg.values()].sort((a, b) => rang(a.cle) - rang(b.cle) || a.cle.localeCompare(b.cle));
}
