import type { ReactNode } from "react";
import { dayLabel, num } from "../lib/format";
import { L } from "../lib/libelles";
import { grouperSources } from "../lib/sources";
import type { Props } from "../lib/types";

/** Éléments communs des fiches : ligne libellé et valeur, contexte, identités successives, sources des listes. */
export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b border-hair/70 py-1.5">
      <span className="text-muted">{label}</span><span className="text-right">{children}</span>
    </div>
  );
}

export function Context({ items }: { items?: string[] }) {
  if (!items?.length) return null;
  return <ul className="list-disc space-y-1 pl-5 text-ink/85">{items.map((t, i) => <li key={i}>{t}</li>)}</ul>;
}

export function vesselText(v?: Props) {
  if (!v) return L.commun.inconnu;
  return `${v.name ?? L.commun.sansNom} (MMSI ${v.mmsi}${v.ship_type ? `, ${v.ship_type}` : ""}${v.length_m ? `, ${num(v.length_m, 0)} m` : ""})`;
}

export function Identities({ rows }: { rows?: Props[] }) {
  if (!rows?.length) return null;
  const P = L.preuves;
  return (
    <table className="mt-1 w-full text-left text-[12px]">
      <thead className="text-muted"><tr><th>{P.nom}</th><th>{P.pavillon}</th><th>{P.mmsi}</th><th>{P.vuDu}</th></tr></thead>
      <tbody>{rows.map((x, i) => (
        <tr key={i} className="border-t border-hair/70">
          <td>{x.name ?? L.fiche.sansNom}</td><td>{x.pavillon ?? x.flag ?? L.commun.nd}</td><td>{x.mmsi}</td>
          <td>{dayLabel(x.premiere_vue ?? x.first_seen)} {P.au} {dayLabel(x.derniere_vue ?? x.last_seen)}</td>
        </tr>))}</tbody>
    </table>
  );
}

/** Sources des listes, une ligne par organisme : motifs et liens vers les fiches. */
export function Sources({ entries }: { entries?: Props[] }) {
  if (!entries?.length) return null;
  const S = L.sources;
  return (
    <ul className="mt-1 space-y-0.5 text-[12px]">
      {grouperSources(entries).map((o) => (
        <li key={o.cle}>
          {S.organismes[o.cle] ?? o.cle}
          {o.motifs.length ? <span className="text-muted"> ({o.motifs.map((m) => S.motifs[m] ?? m).join(", ")})</span> : null}
          {o.liens.map((u, i) => (
            <span key={u}> <a href={u} target="_blank" rel="noreferrer" className="text-signal hover:underline">
              {o.liens.length > 1 ? `${S.fiche} ${i + 1}` : S.fiche}</a></span>
          ))}
        </li>
      ))}
    </ul>
  );
}

export function Tag({ children, color }: { children: ReactNode; color?: string }) {
  return (
    <span className="inline-flex items-center rounded border px-1 text-[10.5px] leading-[15px]"
      style={{ borderColor: color ?? "#26323d", color: color ?? "#7c8b97" }}>{children}</span>
  );
}

/** Séjour prolongé d'un navire des listes (alerte WATCHLIST) : durée, part du temps à l'arrêt, lieu. */
export function Sejour({ s }: { s: Props }) {
  const P = L.preuves;
  const lieu = s.mouillage_connu ? P.sejourMouillage
    : s.distance_cote_km != null ? P.sejourHorsMouillage(num(s.distance_cote_km, 0)) : P.sejourHorsMouillageSeul;
  return (
    <>
      <Row label={P.sejour}>{P.sejourDuree(num(s.duree_h, 0))}</Row>
      <Row label={P.sejourArret}>{num((s.arret_part ?? 0) * 100, 0)} %</Row>
      {s.arret_part > 0 && <Row label={P.sejourLieu}>{lieu}</Row>}
    </>
  );
}
