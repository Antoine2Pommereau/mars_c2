// Recherche dans les réglages des Coulisses : « heures calmes » ouvre Notifications sur sa partie.
import assert from "node:assert/strict";
import { test } from "node:test";
import { chercherReglages, estRubrique, REGLAGES, RUBRIQUES } from "../src/registres/reglages.ts";

test("heures calmes : Notifications, partie Heures calmes, en premier", () => {
  const r = chercherReglages("heures calmes");
  assert.equal(r[0].rubrique, "notifications");
  assert.equal(r[0].partie, "notif-calmes");
  assert.equal(chercherReglages("HEURES  câlmes")[0].partie, "notif-calmes", "casse et accents ignorés");
});

test("les trois tâches de réglage se trouvent par leurs mots", () => {
  assert.equal(chercherReglages("pause sentinel")[0].rubrique, "capteurs");
  assert.equal(chercherReglages("observation")[0].partie, "demande", "le libellé qui contient le mot passe d'abord");
  assert.ok(chercherReglages("observation").some((r) => r.partie === "modes"));
  assert.equal(chercherReglages("règle observation")[0].partie, "modes");
});

test("début de mot seulement, rien pour une requête vide", () => {
  assert.deepEqual(chercherReglages("   "), []);
  assert.equal(chercherReglages("alme").length, 0, "« alme » n'est pas un début de mot");
});

test("chaque réglage mène à une rubrique connue, chaque rubrique a au moins un réglage", () => {
  for (const r of REGLAGES) assert.ok(estRubrique(r.rubrique), r.libelle);
  for (const r of RUBRIQUES) assert.ok(REGLAGES.some((x) => x.rubrique === r), r);
  assert.equal(estRubrique("controle"), false);
});
