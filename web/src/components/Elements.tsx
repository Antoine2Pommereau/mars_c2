import type { ReactNode } from "react";
import { dayLabel, num } from "../lib/format";
import { L } from "../lib/libelles";
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

export function Sources({ entries }: { entries?: Props[] }) {
  if (!entries?.length) return null;
  return (
    <ul className="mt-1 space-y-0.5 text-[12px]">
      {entries.map((e, i) => (
        <li key={i}>
          {e.source === "gur" ? L.sources.gur : L.sources.opensanctions}
          {e.name ? `, ${e.name}` : ""}{e.mmsi ? `, MMSI ${e.mmsi}` : ""}
          {e.risks?.length ? <span className="text-muted"> ({e.risks.join(", ")})</span> : null}
          {e.url && <> <a href={e.url} target="_blank" rel="noreferrer" className="text-signal hover:underline">{L.sources.fiche}</a></>}
        </li>
      ))}
    </ul>
  );
}

/** Pastille discrète (niveau de signal, pavillon) dans une ligne du fil ou un en tête de fiche. */
export function Tag({ children, color }: { children: ReactNode; color?: string }) {
  return (
    <span className="inline-flex items-center rounded border px-1 text-[10.5px] leading-[15px]"
      style={{ borderColor: color ?? "#26323d", color: color ?? "#7c8b97" }}>{children}</span>
  );
}
