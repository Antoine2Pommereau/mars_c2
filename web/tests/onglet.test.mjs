// Onglet du navigateur (lot UX1) : compte des dossiers, couleur de la pastille, titre, mode démonstration.
import assert from "node:assert/strict";
import { test } from "node:test";
import { etatOnglet, faviconOnglet } from "../src/lib/onglet.ts";

const d = (...niveaux) => niveaux.map((niveau) => ({ niveau }));

test("urgents plus à examiner, pastille rouge dès un urgent", () => {
  assert.deepEqual(etatOnglet(d(3, 2, 2, 1, 0), "Veille", false), { titre: "(3) MARS C2 · Veille", pastille: "urgent", compte: 3 });
});

test("à examiner seulement : pastille orange", () => {
  assert.deepEqual(etatOnglet(d(2, 1), "Dossier RORQUAL 401", false),
    { titre: "(1) MARS C2 · Dossier RORQUAL 401", pastille: "examiner", compte: 1 });
});

test("rien à examiner, données pas encore chargées : ni compte ni pastille", () => {
  assert.deepEqual(etatOnglet(d(1, 0), "Coulisses", false), { titre: "MARS C2 · Coulisses", pastille: null, compte: 0 });
  assert.deepEqual(etatOnglet(undefined, "Veille", false), { titre: "MARS C2 · Veille", pastille: null, compte: 0 });
});

test("mode démonstration : rien", () => {
  assert.deepEqual(etatOnglet(d(3, 3, 2), "Veille", true), { titre: "MARS C2 · Veille", pastille: null, compte: 0 });
});

test("favicon : fichier sans pastille, SVG en ligne avec la couleur de l'urgence", () => {
  assert.equal(faviconOnglet(null), "/favicon.svg");
  const rouge = decodeURIComponent(faviconOnglet("urgent"));
  assert.ok(rouge.startsWith("data:image/svg+xml,<svg") && rouge.includes('fill="#ef6461"') && !rouge.includes("M16 2.5v3"));
  assert.ok(decodeURIComponent(faviconOnglet("examiner")).includes('fill="#f0a84b"'));
});
