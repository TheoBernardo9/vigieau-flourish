#!/usr/bin/env python3
"""
Récupère le GeoJSON vigieau (zones de restriction sécheresse) et le prépare pour Flourish.
- Télécharge le GeoJSON brut depuis data.gouv.fr
- Simplifie les propriétés pour Flourish
- Sauvegarde le fichier avec la date du jour + met à jour latest.geojson
"""

import json
import urllib.request
import datetime
import os
import sys
from shapely.geometry import shape, mapping
from shapely.validation import make_valid

GEOJSON_URL = "https://regleau.s3.gra.perf.cloud.ovh.net/geojson/zones_arretes_en_vigueur.geojson"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Correspondance niveaux de restriction → libellés lisibles
NIVEAUX = {
    "vigilance": "Vigilance",
    "alerte": "Alerte",
    "alerte_renforcee": "Alerte renforcée",
    "crise": "Crise",
}

# Ordre de sévérité pour Flourish (couleurs)
SEVERITY = {
    "vigilance": 1,
    "alerte": 2,
    "alerte_renforcee": 3,
    "crise": 4,
}


def fetch_geojson(url: str) -> dict:
    print(f"Téléchargement de {url}…")
    req = urllib.request.Request(url, headers={"User-Agent": "vigieau-flourish/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def simplify_geometry(geom, tolerance=0.001):
    """Simplifie la géométrie avec Douglas-Peucker (tolerance en degrés ≈ 100m)."""
    if not geom:
        return geom
    try:
        s = make_valid(shape(geom))
        simplified = s.simplify(tolerance, preserve_topology=True)
        return mapping(simplified)
    except Exception:
        return geom


def simplify_feature(feature: dict) -> dict:
    """Garde uniquement les champs utiles pour Flourish."""
    p = feature.get("properties") or {}

    niveau = p.get("niveauGravite") or ""
    arrete = p.get("arreteRestriction") or {}
    dept = p.get("departement") or {}

    return {
        "type": "Feature",
        "geometry": simplify_geometry(feature.get("geometry")),
        "properties": {
            "id": p.get("id", ""),
            "nom": p.get("nom", ""),
            "departement_code": dept.get("code", "") if isinstance(dept, dict) else "",
            "departement_nom": dept.get("nom", "") if isinstance(dept, dict) else "",
            "type_zone": p.get("type", ""),
            "niveau": niveau,
            "niveau_label": NIVEAUX.get(niveau, "") if niveau else "",
            "severity": SEVERITY.get(niveau, 0) if niveau else 0,
            "arrete_numero": arrete.get("numero", ""),
            "debut": arrete.get("dateDebut", ""),
            "fin": arrete.get("dateFin", ""),
            "date_signature": arrete.get("dateSignature", ""),
        },
    }


def build_flourish_geojson(raw: dict) -> dict:
    features = [simplify_feature(f) for f in raw.get("features", [])]
    return {
        "type": "FeatureCollection",
        "generated_at": datetime.date.today().isoformat(),
        "total_zones": len(features),
        "features": features,
    }


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()

    raw = fetch_geojson(GEOJSON_URL)
    flourish = build_flourish_geojson(raw)

    # Fichier daté (archivage)
    dated_path = os.path.join(DATA_DIR, f"vigieau_{today}.geojson")
    with open(dated_path, "w", encoding="utf-8") as f:
        json.dump(flourish, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Sauvegardé : {dated_path}  ({flourish['total_zones']} zones)")

    # Fichier latest (branchable directement dans Flourish)
    latest_path = os.path.join(DATA_DIR, "latest.geojson")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(flourish, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Mis à jour  : {latest_path}")

    # Résumé par niveau
    from collections import Counter
    counts = Counter(feat["properties"]["niveau"] for feat in flourish["features"])
    print("\nRépartition des zones :")
    for niveau, label in NIVEAUX.items():
        print(f"  {label:<20} : {counts.get(niveau, 0)}")
    print(f"  Sans restriction    : {counts.get('', 0)}")


if __name__ == "__main__":
    main()
