// Coulisses : quatre groupes, dix rubriques, une place par réglage ; recherche ; anciennes adresses redirigées.
import assert from "node:assert/strict";
import { test } from "node:test";
import { chercherReglages, destination, estRubrique, GROUPES, REGLAGES, RUBRIQUES } from "../src/registres/reglages.ts";

test("quatre groupes, dix rubriques, dans l'ordre demandé, chacune dans un seul groupe", () => {
  assert.deepEqual(GROUPES.map((g) => g.id), ["moi", "surveillance", "capteurs", "plateforme"]);
  assert.deepEqual(RUBRIQUES, ["compte", "notifications", "affichage", "regles", "zones", "sources", "activite", "budget",
    "systeme", "journal"]);
  assert.equal(new Set(RUBRIQUES).size, RUBRIQUES.length);
});

test("chaque réglage a une seule place, et chaque rubrique au moins un réglage", () => {
  const vus = new Set();
  for (const r of REGLAGES) {
    assert.ok(estRubrique(r.rubrique), r.libelle);
    assert.ok(!vus.has(r.libelle), `réglage en double : ${r.libelle}`);
    vus.add(r.libelle);
  }
  for (const r of RUBRIQUES) assert.ok(REGLAGES.some((x) => x.rubrique === r), r);
  assert.ok(!REGLAGES.some((r) => /suivis$|couches/i.test(r.libelle) && r.rubrique !== "zones"), "les outils de la Veille sont sortis");
});

test("recherche : heures calmes, pause d'un capteur, mode d'une règle, vocabulaire d'opérateur", () => {
  assert.deepEqual([chercherReglages("heures calmes")[0].rubrique, chercherReglages("heures calmes")[0].partie], ["notifications", "notif-calmes"]);
  assert.equal(chercherReglages("HEURES  câlmes")[0].partie, "notif-calmes");
  assert.equal(chercherReglages("pause sentinel")[0].rubrique, "sources");
  assert.equal(chercherReglages("règle observation")[0].partie, "modes");
  assert.equal(chercherReglages("vérification satellite")[0].rubrique, "notifications");
  assert.equal(chercherReglages("tip").length, 0, "« tip and cue » n'est plus un mot de l'interface");
  assert.deepEqual(chercherReglages("   "), []);
});

test("ouverture : rubrique demandée, sinon la dernière consultée, sinon Système", () => {
  assert.deepEqual(destination("budget", "regles"), { espace: "coulisses", rubrique: "budget" });
  assert.deepEqual(destination(null, "regles"), { espace: "coulisses", rubrique: "regles" });
  assert.deepEqual(destination(null, "inconnue"), { espace: "coulisses", rubrique: "systeme" });
  assert.deepEqual(destination(undefined, null), { espace: "coulisses", rubrique: "systeme" });
});

test("anciennes adresses redirigées vers la nouvelle place", () => {
  assert.deepEqual(destination("operateur", null), { espace: "coulisses", rubrique: "compte" });
  assert.deepEqual(destination("capteurs", null), { espace: "coulisses", rubrique: "sources" });
  assert.deepEqual(destination("satellites", null), { espace: "coulisses", rubrique: "activite" });
  assert.deepEqual(destination("demo", null), { espace: "coulisses", rubrique: "affichage" });
  assert.deepEqual(destination("suivis", "regles"), { espace: "veille", onglet: "suivis" });
  assert.deepEqual(destination("couches", "regles"), { espace: "veille", couches: true });
  for (const r of ["systeme", "regles", "notifications", "zones", "journal"]) assert.deepEqual(destination(r, null).rubrique, r);
});
