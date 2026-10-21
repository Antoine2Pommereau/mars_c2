// Cadrage de la Veille (lot U) : les dossiers à examiner d'abord, une marge, rien sans dossier.
import assert from "node:assert/strict";
import { test } from "node:test";
import { cadreDossiers } from "../src/lib/cadre.ts";

test("les dossiers à examiner seuls, avec la marge", () => {
  const b = cadreDossiers([{ lon: -0.15, lat: 50.45, niveau: 3 }, { lon: 1.2, lat: 50.9, niveau: 2 }, { lon: 7.2, lat: 43.6, niveau: 1 }]);
  assert.deepEqual(b.map((x) => +x.toFixed(2)), [-0.45, 50.15, 1.5, 51.2]);
});

test("sans dossier à examiner, tous ; sans dossier, rien", () => {
  assert.deepEqual(cadreDossiers([{ lon: 5, lat: 43, niveau: 1 }], 0.5), [4.5, 42.5, 5.5, 43.5]);
  assert.equal(cadreDossiers([]), null);
});
