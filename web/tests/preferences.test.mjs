// Préférences par navigateur : défaut sans stockage, valeur gardée, valeur inconnue ignorée.
import assert from "node:assert/strict";
import { test } from "node:test";
import { garderRubrique, garderVignettes, garderVueDefaut, lireRubrique, lireVignettes, lireVueDefaut } from "../src/lib/preferences.ts";

test("sans stockage : valeurs par défaut, aucune erreur", () => {
  assert.equal(lireVueDefaut(), "veille");
  assert.equal(lireVignettes(), false);
  assert.equal(lireRubrique(), null);
  assert.doesNotThrow(() => { garderVueDefaut("trafic"); garderVignettes(true); garderRubrique("budget"); });
});

test("avec stockage : gardées, et une vue inconnue revient à la Veille", () => {
  const m = new Map();
  globalThis.window = { localStorage: { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v) } };
  garderVueDefaut("satellites"); garderVignettes(true); garderRubrique("budget");
  assert.deepEqual([lireVueDefaut(), lireVignettes(), lireRubrique()], ["satellites", true, "budget"]);
  m.set("mars.vue.defaut", "couches");
  assert.equal(lireVueDefaut(), "veille");
  delete globalThis.window;
});
