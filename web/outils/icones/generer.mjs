// Icônes, manifeste et image d'aperçu de MARS C2, générés depuis le symbole (web/src/lib/symbole.ts) ; les fichiers
// produits sont versionnés dans web/public (et l'aperçu social dans docs/images). À relancer après tout changement
// du symbole :   cd web/outils/icones && npm ci && npm run generer
// Polices IBM Plex (Fontsource, licence OFL) lues en WOFF et décodées en TTF pour le moteur de rendu (resvg).
import { Resvg } from "@resvg/resvg-js";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";
import { COULEUR_SYMBOLE as C, FOND, symboleSvg } from "../../src/lib/symbole.ts";

const ICI = dirname(fileURLToPath(import.meta.url));
const PUBLIC = join(ICI, "../../public");
const IMAGES = join(ICI, "../../../docs/images");
mkdirSync(PUBLIC, { recursive: true });
mkdirSync(IMAGES, { recursive: true });

/** WOFF 1 vers TTF : en tête sfnt, répertoire des tables, tables décompressées (zlib) et alignées sur 4 octets. */
function woffEnTtf(woff) {
  const n = woff.readUInt16BE(12), flavor = woff.readUInt32BE(4);
  const tables = [];
  for (let i = 0; i < n; i++) {
    const e = 44 + i * 20;
    const [tag, off, comp, orig, sum] = [woff.readUInt32BE(e), woff.readUInt32BE(e + 4), woff.readUInt32BE(e + 8),
      woff.readUInt32BE(e + 12), woff.readUInt32BE(e + 16)];
    const brut = woff.subarray(off, off + comp);
    tables.push({ tag, sum, data: comp < orig ? inflateSync(brut) : Buffer.from(brut) });
  }
  const tete = Buffer.alloc(12 + 16 * n);
  const p2 = 2 ** Math.floor(Math.log2(n));
  tete.writeUInt32BE(flavor, 0); tete.writeUInt16BE(n, 4); tete.writeUInt16BE(p2 * 16, 6);
  tete.writeUInt16BE(Math.log2(p2), 8); tete.writeUInt16BE(n * 16 - p2 * 16, 10);
  let off = tete.length;
  const corps = [];
  tables.forEach((t, i) => {
    const e = 12 + i * 16;
    tete.writeUInt32BE(t.tag, e); tete.writeUInt32BE(t.sum, e + 4); tete.writeUInt32BE(off, e + 8);
    tete.writeUInt32BE(t.data.length, e + 12);
    const pad = (4 - (t.data.length % 4)) % 4;
    corps.push(t.data, Buffer.alloc(pad));
    off += t.data.length + pad;
  });
  return Buffer.concat([tete, ...corps]);
}

const TMP = mkdtempSync(join(tmpdir(), "mars-polices-"));
const POLICES = [
  "@fontsource/ibm-plex-sans-condensed/files/ibm-plex-sans-condensed-latin-600-normal.woff",
  "@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-400-normal.woff",
  "@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-500-normal.woff",
].map((f, i) => {
  const ttf = join(TMP, `${i}.ttf`);
  writeFileSync(ttf, woffEnTtf(readFileSync(join(ICI, "node_modules", f))));
  return ttf;
});

const png = (svg, largeur) => new Resvg(svg, {
  fitTo: { mode: "width", value: largeur },
  font: { fontFiles: POLICES, loadSystemFonts: false, defaultFontFamily: "IBM Plex Sans" },
}).render().asPng();

function ecrire(nom, contenu, dossier = PUBLIC) {
  writeFileSync(join(dossier, nom), contenu);
  console.log(`${nom.padEnd(22)} ${String(contenu.length).padStart(7)} octets`);
}

// Favicon : version 16 (sans repères), couleur selon le thème du système, pour l'onglet
ecrire("favicon.svg", symboleSvg({ couleur: C, petit: true, titre: "MARS C2" }));
ecrire("favicon-32.png", png(symboleSvg({ couleur: C.sombre }), 32));
ecrire("favicon-16.png", png(symboleSvg({ couleur: C.sombre, petit: true }), 16));
// Icônes d'application : symbole sur carré arrondi
ecrire("apple-touch-icon.png", png(symboleSvg({ couleur: C.sombre, carre: FOND.carre }), 180));
ecrire("icon-512.png", png(symboleSvg({ couleur: C.sombre, carre: FOND.carre }), 512));
ecrire("site.webmanifest", JSON.stringify({
  name: "MARS C2", short_name: "MARS C2", description: "Surveillance maritime : infrastructures et flotte fantôme",
  start_url: "/", display: "standalone", theme_color: FOND.sombre, background_color: FOND.sombre,
  icons: [
    { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
    { src: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" },
    { src: "/favicon.svg", sizes: "any", type: "image/svg+xml" },
  ],
}, null, 2) + "\n");

// Image d'aperçu 1200 × 630 : symbole, nom, sous titre, mention
const symbole = symboleSvg({ couleur: C.sombre }).replace("<svg ", '<svg x="150" y="195" width="240" height="240" ');
const apercu = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="${FOND.sombre}"/>
  <rect x="0" y="0" width="1200" height="630" fill="none" stroke="#26323d" stroke-width="2"/>
  ${symbole}
  <text x="450" y="318" font-family="IBM Plex Sans Condensed" font-weight="600" font-size="112" letter-spacing="13.4"
        fill="#e6ecf0">MARS C2</text>
  <text x="454" y="382" font-family="IBM Plex Sans" font-weight="400" font-size="40" fill="#7c8b97">Surveillance maritime</text>
  <text x="454" y="560" font-family="IBM Plex Sans" font-weight="500" font-size="22" letter-spacing="3.5"
        fill="#7c8b97">DONNÉES OUVERTES · PROJET PERSONNEL</text>
</svg>`;
const og = png(apercu, 1200);
ecrire("og.png", og);
ecrire("apercu_social.png", og, IMAGES);

// Pages d'erreur servies par nginx (web/nginx.conf) : 404, 503 (mise à jour en cours, aussi pour une API injoignable
// pendant un déploiement), 502 (délai dépassé). Même logotype ; les pages d'attente reviennent seules à la page
// d'origine dès que /api/health répond (erreurs/retour.js, script du site : la politique de contenu interdit le script
// en ligne).
const ERREURS = join(PUBLIC, "erreurs");
mkdirSync(ERREURS, { recursive: true });
const logo = symboleSvg({ couleur: "currentColor", enLigne: true, px: 28 });
function page(titre, texte, { attente = false, lien = false } = {}) {
  return `<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>MARS C2 · ${titre}</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@600&family=IBM+Plex+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--fond:#0e1419;--panneau:#141c23;--filet:#26323d;--encre:#e6ecf0;--second:#8d9ba6;--symbole:#4fb6c8}
@media (prefers-color-scheme: light){:root{--fond:#e9edf1;--panneau:#f7f9fa;--filet:#d3dbe2;--encre:#17222b;--second:#5b6b78;--symbole:#1f7f91}}
html,body{height:100%;margin:0}
body{display:grid;place-items:center;background:var(--fond);color:var(--encre);font:14px/1.5 "IBM Plex Sans",system-ui,sans-serif;padding:16px;box-sizing:border-box}
main{max-width:400px;width:100%;box-sizing:border-box;padding:28px;border:1px solid var(--filet);border-radius:10px;background:var(--panneau)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:24px;color:var(--symbole)}
.nom{display:block;font:600 14px/1 "IBM Plex Sans Condensed","IBM Plex Sans",system-ui,sans-serif;letter-spacing:.12em;color:var(--encre)}
.sous{display:block;margin-top:3px;font-size:10px;line-height:1;color:var(--second)}
h1{margin:0 0 6px;font-size:17px;font-weight:600}
p{margin:0;color:var(--second)}
a{color:var(--symbole)}
.attente{margin-top:16px;font-size:12px}
.mention{margin-top:24px;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--second)}
</style>
</head>
<body>
<main>
<div class="logo">${logo}<span><span class="nom">MARS C2</span><span class="sous">Surveillance maritime</span></span></div>
<h1>${titre}</h1>
<p>${texte}</p>
${attente ? '<p class="attente" id="attente">Nouvel essai toutes les cinq secondes.</p>\n<script src="/erreurs/retour.js" defer></script>' : ""}${lien ? '<p class="attente"><a href="/">Revenir à la Veille</a></p>' : ""}
<p class="mention">Données ouvertes · Projet personnel</p>
</main>
</body>
</html>
`;
}
ecrire("erreurs/404.html", page("Page introuvable", "Cette adresse ne correspond à aucune page de MARS C2.", { lien: true }));
ecrire("erreurs/503.html", page("Mise à jour en cours", "Retour dans une minute : cette page se recharge seule dès que le service répond.", { attente: true }));
ecrire("erreurs/502.html", page("Le service ne répond pas", "Le serveur de MARS C2 met trop de temps à répondre. Cette page se recharge seule dès qu'il répond.", { attente: true }));
ecrire("erreurs/retour.js", `// Pages d'attente (503, 502) : interroge /api/health toutes les cinq secondes et revient à la page d'origine
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
`);
