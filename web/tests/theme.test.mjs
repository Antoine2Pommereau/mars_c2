// Thème clair (lib/theme.ts) : couleurs de la carte teintes, expressions MapLibre parcourues, contraste sur le fond
// Positron mesuré plutôt qu'affirmé.
import assert from "node:assert/strict";
import { test } from "node:test";
import { TEINTES_CLAIR, teinter } from "../src/lib/theme.ts";

test("le thème sombre ne change rien", () => {
  const p = { "circle-color": ["match", ["get", "statut"], "sans_ais", "#e85bc7", "#e6ecf0"] };
  assert.equal(teinter(p, "sombre"), p);
});

test("le clair teint les expressions, garde les couleurs d'alerte et les clés", () => {
  const p = { "circle-color": ["match", ["get", "statut"], "ecartee", "#4c5a66", "sans_ais", "#e85bc7", "#E6ECF0"],
              "circle-stroke-width": 1.5, "text-halo-color": "#0e1419" };
  assert.deepEqual(teinter(p, "clair"), { "circle-color": ["match", ["get", "statut"], "ecartee", "#9aa6b0", "sans_ais",
    "#e85bc7", "#1d2933"], "circle-stroke-width": 1.5, "text-halo-color": "#ffffff" });
  assert.equal(teinter("#ef6461", "clair"), "#ef6461");
});

const lum = (hex) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const contraste = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };

test("sur le fond Positron, les traits et points lisibles atteignent 3:1 (poussière et écartées exceptées, voulues pâles)", () => {
  const pales = new Set(["#ffffff", "#9aa6b0", "#a3afb9"]);
  for (const [sombre, clair] of Object.entries(TEINTES_CLAIR)) {
    if (!clair.startsWith("#") || pales.has(clair)) continue;
    assert.ok(contraste(clair, "#fafaf8") >= 3, `${sombre} devient ${clair} : ${contraste(clair, "#fafaf8").toFixed(2)}:1`);
  }
});
