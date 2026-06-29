# vigieau-flourish

GeoJSON quotidien des restrictions sécheresse (vigieau) prêt à brancher sur Flourish.

## Fonctionnement

Un GitHub Action tourne chaque matin à 9h (Paris) et :
1. Télécharge le GeoJSON officiel de [data.gouv.fr](https://www.data.gouv.fr/datasets/donnee-secheresse-vigieau/)
2. Simplifie les propriétés pour Flourish
3. Commit `data/latest.geojson` + `data/vigieau_YYYY-MM-DD.geojson`

## Brancher sur Flourish

URL du fichier `latest.geojson` (une fois le repo public) :
```
https://raw.githubusercontent.com/TON_COMPTE/vigieau-flourish/main/data/latest.geojson
```

Dans Flourish → **Map** → **Boundaries** → coller l'URL raw GitHub.

## Propriétés disponibles dans le GeoJSON

| Champ | Description |
|---|---|
| `nom` | Nom de la zone |
| `departement` | Code département |
| `type_zone` | Type (SOU = souterrain, SUP = superficiel, AEP = eau potable) |
| `niveau_robinet` | Niveau pour l'eau du robinet |
| `niveau_cours_eau` | Niveau pour les cours d'eau |
| `niveau_nappe` | Niveau pour les nappes |
| `niveau_max` | Niveau le plus sévère (vigilance / alerte / alerte_renforcee / crise) |
| `niveau_max_label` | Libellé lisible du niveau max |
| `severity` | Score 1–4 (pour colorier Flourish) |
| `debut` / `fin` | Dates de validité de l'arrêté |

## Lancer manuellement

```bash
python fetch_vigieau.py
```

Source : [VigiEau / data.gouv.fr](https://www.data.gouv.fr/datasets/donnee-secheresse-vigieau/) — Licence Ouverte Etalab 2.0
