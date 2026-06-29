#!/usr/bin/env python3
"""
Récupère le GeoJSON vigieau (zones de restriction sécheresse) et le prépare pour Flourish.
- Départements France métropole + Corse en base (severity=0, "Aucune restriction")
- Zones avec arrêtés actifs par-dessus (severity 1-4)
- Sauvegarde le fichier avec la date du jour + met à jour latest.geojson
"""

import json
import urllib.request
import datetime
import os
from collections import Counter
from shapely.geometry import shape, mapping
from shapely.validation import make_valid

GEOJSON_URL = "https://regleau.s3.gra.perf.cloud.ovh.net/geojson/zones_arretes_en_vigueur.geojson"
DEPTS_URL = "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/departements-version-simplifiee.geojson"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

NIVEAUX = {
    "vigilance": "Vigilance",
    "alerte": "Alerte",
    "alerte_renforcee": "Alerte renforcée",
    "crise": "Crise",
}

SEVERITY = {
    "vigilance": 1,
    "alerte": 2,
    "alerte_renforcee": 3,
    "crise": 4,
}

TYPE_ZONE = {
    "SUP": "Eaux de surface",
    "SOU": "Eaux souterraines",
    "AEP": "Eau du robinet",
}

def format_date(d: str) -> str:
    """2026-06-27 → 27 juin 2026"""
    if not d:
        return ""
    mois = ["jan.", "fév.", "mars", "avr.", "mai", "juin",
            "juil.", "août", "sept.", "oct.", "nov.", "déc."]
    try:
        y, m, day = d.split("-")
        return f"{int(day)} {mois[int(m)-1]} {y}"
    except Exception:
        return d

def build_detail(type_zone: str, niveau_label: str, debut: str, fin: str, nom: str) -> str:
    type_label = TYPE_ZONE.get(type_zone, type_zone)
    parts = [type_label]
    if niveau_label:
        parts.append(f"• {niveau_label}")
    if debut and fin:
        parts.append(f"du {format_date(debut)} au {format_date(fin)}")
    elif debut:
        parts.append(f"depuis le {format_date(debut)}")
    if nom:
        parts.append(nom)
    return "\n".join(parts)


def fetch_geojson(url: str) -> dict:
    print(f"Téléchargement de {url}…")
    req = urllib.request.Request(url, headers={"User-Agent": "vigieau-flourish/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def simplify_geometry(geom, tolerance=0.001):
    if not geom:
        return geom
    try:
        s = make_valid(shape(geom))
        simplified = s.simplify(tolerance, preserve_topology=True)
        return mapping(simplified)
    except Exception:
        return geom


def dept_features(depts_geojson: dict) -> list:
    """Construit les features de base (departements = aucune restriction)."""
    features = []
    for f in depts_geojson.get("features", []):
        p = f.get("properties") or {}
        features.append({
            "type": "Feature",
            "geometry": simplify_geometry(f.get("geometry"), tolerance=0.002),
            "properties": {
                "id": f"dept_{p.get('code', '')}",
                "nom": p.get("nom", ""),
                "departement_code": p.get("code", ""),
                "departement_nom": p.get("nom", ""),
                "type_zone": "departement",
                "niveau": "",
                "niveau_label": "Aucune restriction",
                "severity": "",
                "arrete_numero": "",
                "debut": "",
                "fin": "",
                "date_signature": "",
            },
        })
    return features


def zone_feature(feature: dict) -> dict:
    """Simplifie une zone avec arrêté actif."""
    p = feature.get("properties") or {}
    niveau = p.get("niveauGravite") or ""
    arrete = p.get("arreteRestriction") or {}
    dept = p.get("departement") or {}
    dept_code = dept.get("code", "") if isinstance(dept, dict) else ""

    # Filtrer DOM-TOM (codes dept >= 97, sauf 2A/2B)
    if dept_code and dept_code not in ("2A", "2B"):
        try:
            if int(dept_code) >= 97:
                return None
        except ValueError:
            pass

    return {
        "type": "Feature",
        "geometry": simplify_geometry(feature.get("geometry")),
        "properties": {
            "id": p.get("id", ""),
            "nom": p.get("nom", ""),
            "departement_code": dept_code,
            "departement_nom": dept.get("nom", "") if isinstance(dept, dict) else "",
            "type_zone": p.get("type", ""),
            "niveau": niveau,
            "niveau_label": NIVEAUX.get(niveau, niveau),
            "severity": SEVERITY.get(niveau, 0),
            "arrete_numero": arrete.get("numero", ""),
            "debut": arrete.get("dateDebut", ""),
            "fin": arrete.get("dateFin", ""),
            "date_signature": arrete.get("dateSignature", ""),
            "detail": build_detail(
                p.get("type", ""),
                NIVEAUX.get(niveau, niveau),
                arrete.get("dateDebut", ""),
                arrete.get("dateFin", ""),
                p.get("nom", ""),
            ),
        },
    }


def build_flourish_geojson(depts: dict, zones: dict) -> dict:
    # Base : départements métropole + Corse (déjà filtrés dans le fichier gregoiredavid)
    features = dept_features(depts)

    # Par-dessus : zones avec restrictions (filtrées DOM-TOM)
    for f in zones.get("features", []):
        feat = zone_feature(f)
        if feat is not None:
            features.append(feat)

    return {
        "type": "FeatureCollection",
        "generated_at": datetime.date.today().isoformat(),
        "total_features": len(features),
        "features": features,
    }


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()

    depts = fetch_geojson(DEPTS_URL)
    zones = fetch_geojson(GEOJSON_URL)
    flourish = build_flourish_geojson(depts, zones)

    dated_path = os.path.join(DATA_DIR, f"vigieau_{today}.geojson")
    with open(dated_path, "w", encoding="utf-8") as f:
        json.dump(flourish, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Sauvegardé : {dated_path}  ({flourish['total_features']} features)")

    latest_path = os.path.join(DATA_DIR, "latest.geojson")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(flourish, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Mis à jour  : {latest_path}")

    counts = Counter(feat["properties"]["niveau"] for feat in flourish["features"])
    print("\nRépartition :")
    print(f"  Départements base   : {sum(1 for f in flourish['features'] if f['properties']['type_zone'] == 'departement')}")
    for niveau, label in NIVEAUX.items():
        print(f"  {label:<20} : {counts.get(niveau, 0)}")


if __name__ == "__main__":
    main()
