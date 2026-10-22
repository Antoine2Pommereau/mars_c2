import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../lib/api";
import { L } from "../lib/libelles";
import { enregistrerDemandeur } from "../lib/session";

const C = L.controle;
const champ = "w-full rounded border border-hair bg-abyss px-2 py-1.5 text-[12.5px] text-ink placeholder:text-faint focus:border-signal focus:outline-none";

/** Fenêtre de connexion ouverte par une écriture refusée faute de session (lib/session.ts) : une fois connecté,
 *  l'écriture en attente reprend ; Échap ou « Renoncer » la laisse échouer avec son motif. En lecture, rien ne change.
 *  Montée une fois, à la racine ; un portail la sort des panneaux à flou d'arrière plan. */
export default function ConnexionDemandee() {
  const qc = useQueryClient();
  const attente = useRef<((ok: boolean) => void)[]>([]);
  const [ouverte, setOuverte] = useState(false);
  const [nom, setNom] = useState(""), [mdp, setMdp] = useState("");
  const [erreur, setErreur] = useState<string | null>(null), [envoi, setEnvoi] = useState(false);

  useEffect(() => {
    enregistrerDemandeur(() => new Promise<boolean>((resolve) => { attente.current.push(resolve); setOuverte(true); }));
    return () => enregistrerDemandeur(null);
  }, []);
  const finir = (ok: boolean) => {
    for (const r of attente.current.splice(0)) r(ok);
    setOuverte(false); setMdp(""); setErreur(null);
  };
  useEffect(() => {
    if (!ouverte) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); finir(false); } };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [ouverte]);

  if (!ouverte) return null;
  const entrer = async () => {
    setEnvoi(true); setErreur(null);
    try {
      await api.controle.connexion(nom.trim(), mdp);
      await qc.invalidateQueries({ queryKey: ["controle"] });
      finir(true);
    } catch (e) {
      setErreur((e as Error).message);
    } finally {
      setEnvoi(false);
    }
  };
  return createPortal(
    <div role="dialog" aria-modal="true" aria-label={C.demandee.titre} data-connexion-demandee
      className="fixed inset-0 z-[60] flex items-center justify-center bg-abyss/70 p-6" onClick={() => finir(false)}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); entrer(); }}
        className="w-80 space-y-2 rounded-lg border border-hair bg-panel p-4 shadow-2xl">
        <h2 className="text-[14px] font-semibold text-ink">{C.demandee.titre}</h2>
        <p className="text-[12px] text-muted">{C.demandee.texte}</p>
        <input className={champ} placeholder={C.operateur} value={nom} onChange={(e) => setNom(e.target.value)} autoFocus
          autoComplete="username" aria-label={C.operateur} />
        <input className={champ} placeholder={C.motDePasse} type="password" value={mdp} onChange={(e) => setMdp(e.target.value)}
          autoComplete="current-password" aria-label={C.motDePasse} />
        {erreur && <p className="text-[11.5px] text-gap">{erreur}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={() => finir(false)} className="rounded border border-hair px-2.5 py-1 text-[12px] text-muted hover:text-ink">{C.demandee.renoncer}</button>
          <button type="submit" disabled={!nom.trim() || !mdp || envoi}
            className="rounded border border-signal/60 px-2.5 py-1 text-[12px] text-ink hover:border-signal disabled:opacity-40">{C.seConnecter}</button>
        </div>
      </form>
    </div>, document.body);
}
