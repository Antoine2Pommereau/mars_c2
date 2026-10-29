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
