// Preuve d'un dossier (lot U, étape 4) : la carte et la légende donnent les mêmes distances ; cercle au bon rayon.
import assert from "node:assert/strict";
import { test } from "node:test";
import { cercle, distance, emprise, preuveGeoJSON } from "../src/lib/preuve.ts";

// GOELAND 466 sur le jeu d'essai : cercle de 9 km, écho à 4,1 km de la position estimée
const PREUVE = {
  derniere_position: { lon: -0.20247, lat: 50.47372, t: "2026-10-09T05:58:49+00:00" },
  echo: { lon: -0.15247, lat: 50.45372, t: "2026-10-09T18:12:00+00:00", longueur_m: 182, source: "sentinel1" },
  estimee: { lon: -0.21027, lat: 50.45372, rayon_m: 9000 },
  distance_estimee_echo_m: 4105, distance_derniere_echo_m: 4180,
};

test("distances des preuves en milles nautiques, mètres sous 0,1 mille (lot UX1)", () => {
  assert.equal(distance(4105), "2,2 NM");
  assert.equal(distance(12_400), "6,7 NM");
  assert.equal(distance(847), "0,46 NM");
  assert.equal(distance(150), "150 m");
});

test("la carte porte la distance de la légende, et chaque rôle une fois", () => {
  const g = preuveGeoJSON(PREUVE, [[-0.3, 50.5, "a"], [-0.2, 50.47, "b"]]);
  const roles = g.features.map((f) => f.properties.role).sort();
  assert.deepEqual(roles, ["cercle", "derniere", "ecart", "echo", "estimee", "route", "silence"]);
  assert.equal(g.features.find((f) => f.properties.role === "ecart").properties.etiquette, distance(PREUVE.distance_estimee_echo_m));
});

test("le cercle a le rayon annoncé (à 1 % près)", () => {
  const c = cercle(-0.21027, 50.45372, 9000);
  const [lon, lat] = c[16];                          // un quart de tour : plein nord
  assert.ok(Math.abs((lat - 50.45372) * 111_320 - 9000) < 90);
  assert.equal(lon.toFixed(5), "-0.21027");
  assert.deepEqual(c[0], c[c.length - 1]);
});

test("sans preuve, rien à dessiner ; l'emprise couvre le cercle", () => {
  assert.equal(preuveGeoJSON(null).features.length, 0);
  assert.equal(emprise(null), null);
  const [o, s, e, n] = emprise(PREUVE);
  assert.ok(n - s > 0.16 && o < -0.33 && e > -0.16);
});
