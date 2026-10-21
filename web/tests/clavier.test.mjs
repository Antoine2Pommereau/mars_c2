// Triage au clavier (lot U, étape 5) : touches ignorées en saisie ou avec modification ; curseur borné.
import assert from "node:assert/strict";
import { test } from "node:test";
import { deplacer, toucheLibre } from "../src/lib/clavier.ts";

test("une touche de triage hors saisie, en minuscule", () => {
  assert.equal(toucheLibre({ key: "J" }), "j");
  assert.equal(toucheLibre({ key: "Enter", target: { tagName: "BUTTON" } }), "enter");
  assert.equal(toucheLibre({ key: "?" }), "?");
});

test("rien pendant une saisie ou avec une touche de modification", () => {
  assert.equal(toucheLibre({ key: "c", target: { tagName: "TEXTAREA" } }), null);
  assert.equal(toucheLibre({ key: "c", target: { tagName: "INPUT" } }), null);
  assert.equal(toucheLibre({ key: "v", target: { isContentEditable: true } }), null);
  assert.equal(toucheLibre({ key: "k", metaKey: true }), null, "Cmd + K reste la recherche");
});

test("le curseur part du début, avance, recule et reste borné", () => {
  const l = ["navire:1", "navire:2", "point:9"];
  assert.equal(deplacer(l, null, 1), "navire:1");
  assert.equal(deplacer(l, null, -1), "point:9");
  assert.equal(deplacer(l, "navire:1", 1), "navire:2");
  assert.equal(deplacer(l, "point:9", 1), "point:9");
  assert.equal(deplacer(l, "navire:1", -1), "navire:1");
  assert.equal(deplacer(l, "disparu", 1), "navire:1", "dossier clos entre temps : on repart du début");
  assert.equal(deplacer([], null, 1), null);
});
