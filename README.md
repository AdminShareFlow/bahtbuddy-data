# Baht Buddy — données

Données publiques utilisées par l'appli Android **Baht Buddy** (distributeurs, banques et
bureaux de change en Thaïlande, frais des banques).

| Fichier | Contenu | Mis à jour |
|---|---|---|
| `meta.json` | Date et taille des données : l'appli le consulte pour savoir s'il y a du nouveau | chaque semaine (robot) |
| `places-th.json` | Tous les lieux : `[id, lat, lon, type, banque, nom, horaires, atm]` — type `a` = ATM, `b` = banque, `x` = bureau de change | chaque semaine (robot) |
| `banks.json` | Banques, mots-clés de reconnaissance et frais par carte (Visa, Mastercard…) | **à la main** quand un tarif change |

## Fonctionnement

Chaque lundi, [le robot](.github/workflows/update.yml) lance `scripts/build.py` :
1. OpenStreetMap (via Overpass), carré par carré. Une zone qui échoue garde les données de la semaine d'avant.
2. Overture Maps (dernière version), ajoutée sans doublons (même type à moins de 60 m).
3. Garde-fou : si le total chute de plus de 30 %, rien n'est publié.

Pour lancer une mise à jour tout de suite : onglet **Actions** → « Mise à jour des lieux » → **Run workflow**.

## Changer un tarif

Modifier `banks.json` directement sur GitHub (icône crayon), par exemple `"mc": 350`, et mettre
`"checked"` à jour (`"2026-11"`). Les téléphones récupèrent le changement à leur prochaine vérification.

## Licences

- Lieux OpenStreetMap : © contributeurs OpenStreetMap, [ODbL](https://opendatacommons.org/licenses/odbl/).
- Lieux Overture Maps : © Overture Maps Foundation, [CDLA-Permissive-2.0](https://cdla.dev/permissive-2-0/).
- Contour de la Thaïlande : Overture Maps (divisions).
