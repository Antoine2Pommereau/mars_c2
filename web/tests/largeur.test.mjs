// Panneaux redimensionnables : bornes, moitié de l'écran, stockage absent sans erreur.
import assert from "node:assert/strict";
import { test } from "node:test";
import { bornerLargeur, ecrireLargeur, lireLargeur } from "../src/lib/largeur.ts";

test("bornée entre 280 et 640 px, et à la moitié de l'écran", () => {
  assert.equal(bornerLargeur(200, 280, 640, 1440), 280);
  assert.equal(bornerLargeur(900, 280, 640, 1440), 640);
  assert.equal(bornerLargeur(600, 280, 640, 1000), 500, "moitié d'un écran de 1000 px");
  assert.equal(bornerLargeur(600, 280, 640, 400), 280, "écran très étroit : le minimum l'emporte");
  assert.equal(bornerLargeur(Number.NaN, 280, 640, 1440), 280);
});

test("sans stockage (navigation privée, Node) : valeur par défaut, aucune erreur", () => {
  assert.equal(lireLargeur("veille", 350), 350);
  assert.doesNotThrow(() => ecrireLargeur("veille", 420));
});

test("stockage présent : la largeur revient", () => {
  const m = new Map();
  globalThis.window = { localStorage: { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v) } };
  ecrireLargeur("veille", 421.6);
  assert.equal(lireLargeur("veille", 350), 422);
  m.set("mars.largeur.veille", "abc");
  assert.equal(lireLargeur("veille", 350), 350, "valeur illisible : défaut");
  globalThis.window = { get localStorage() { throw new Error("bloqué"); } };
  assert.equal(lireLargeur("veille", 350), 350);
  delete globalThis.window;
});
