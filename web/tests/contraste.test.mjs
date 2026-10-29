// Contraste du texte (lot UX1) : chaque jeton de texte atteint 4,5:1 sur chaque fond, dans les deux thèmes ; lu dans
// styles.css (thème sombre dans @theme, clair sous [data-theme="light"]), mesuré selon la formule du WCAG.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const jetons = (bloc) => Object.fromEntries([...bloc.matchAll(/--color-([a-z0-9]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2]]));
const sombre = jetons(css.slice(css.indexOf("@theme"), css.indexOf("}", css.indexOf("@theme"))));
const debutClair = css.indexOf(':root[data-theme="light"] {');
const clair = { ...sombre, ...jetons(css.slice(debutClair, css.indexOf("}", debutClair))) };

const lum = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
  .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
const contraste = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };

const FONDS = ["abyss", "panel", "raised"];
const TEXTES = ["ink", "muted", "faint", "signal", "gap", "rdv", "dark", "unconf", "watch"];

for (const [nom, t] of Object.entries({ sombre, clair })) {
  test(`thème ${nom} : texte à 4,5:1 au moins sur chaque fond`, () => {
    for (const x of TEXTES) for (const f of FONDS) {
      const c = contraste(t[x], t[f]);
      assert.ok(c >= 4.5, `${x} (${t[x]}) sur ${f} (${t[f]}) : ${c.toFixed(2)}:1`);
    }
  });
}
