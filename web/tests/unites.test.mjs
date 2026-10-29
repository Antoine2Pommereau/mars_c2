// Unités marines (lot UX1) : coordonnées, milles nautiques, nœuds, groupe date heure, textes de l'API convertis.
import assert from "node:assert/strict";
import { test } from "node:test";
import { coord, distance, distanceKm, gdh, heureParis, texteMarin, vitesse } from "../src/lib/unites.ts";

test("coordonnées en degrés et minutes décimales, latitude d'abord, minutes sur deux chiffres", () => {
  assert.equal(coord(-4.205, 48.645), "48°38,7′ N 4°12,3′ O");
  assert.equal(coord(1.78796, 51.15635), "51°09,4′ N 1°47,3′ E");
  assert.equal(coord(-0.5, -0.99999), "1°00,0′ S 0°30,0′ O");          // 59,9994′ arrondi : degré suivant
  assert.equal(coord(-4.205, 48.645, "dd"), "48,6450° N 4,2050° O");
});

test("milles nautiques : deux décimales sous 1 mille, une au delà ; mètres sous 0,1 mille pour une preuve fine", () => {
  assert.equal(distance(487), "0,26 NM");
  assert.equal(distance(487, true), "0,26 NM");
  assert.equal(distance(150, true), "150 m");
  assert.equal(distance(150), "0,08 NM");
  assert.equal(distance(11_477), "6,2 NM");
  assert.equal(distanceKm(6.2), "3,3 NM");
  assert.equal(distance(null), "n.d.");
  assert.equal(vitesse(8.8), "8,8 nœuds");
});

test("groupe date heure et heure de Paris", () => {
  assert.equal(gdh("2026-10-29T16:54:00Z"), "291654Z OCT 26");
  assert.equal(heureParis("2026-10-29T16:54:00Z"), "29/10/2026 17:54 heure de Paris");
  assert.equal(heureParis("2026-07-01T16:54:00Z"), "01/07/2026 18:54 heure de Paris");
});

test("textes de l'API : distances converties, longueurs et surfaces gardées", () => {
  assert.equal(texteMarin("À 487 m d'une infrastructure (Câble télécom)"), "À 0,26 NM d'une infrastructure (Câble télécom)");
  assert.equal(texteMarin("Navire des listes à 6,2 km : ALTAI"), "Navire des listes à 3,3 NM : ALTAI");
  assert.equal(texteMarin("second écho à 107 m d'un écho apparié"), "second écho à 107 m d'un écho apparié");
  assert.equal(texteMarin("écho de 30 m, longueur 34 m contre 119 m, zone de 12 km²"),
    "écho de 30 m, longueur 34 m contre 119 m, zone de 12 km²");
});
