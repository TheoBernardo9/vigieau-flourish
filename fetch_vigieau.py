#!/usr/bin/env python3
"""
Récupère le GeoJSON vigieau (zones de restriction sécheresse) et le prépare pour Flourish.
- Départements France métropole + Corse en base (sans restriction)
- Zones avec arrêtés actifs par-dessus (severity 1-4)
- Popup detail avec les 3 couches (SOU / SUP / AEP) regroupées par arrêté
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
LATEST_DIR = os.path.join(DATA_DIR, "latest")
ARCHIVES_DIR = os.path.join(DATA_DIR, "archives")

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

# Ordre d'affichage dans le popup (comme vigieau.gouv.fr)
TYPE_ORDER = ["SOU", "SUP", "AEP"]
TYPE_LABEL = {
    "SOU": "Eaux souterraines",
    "SUP": "Eaux de surface",
    "AEP": "Eau du robinet",
}

MOIS = ["jan.", "fév.", "mars", "avr.", "mai", "juin",
        "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def format_date(d) -> str:
    if not d:
        return ""
    try:
        y, m, day = str(d).split("-")
        return f"{int(day)} {MOIS[int(m)-1]} {y}"
    except Exception:
        return str(d)


def is_dom_tom(dept_code: str) -> bool:
    if dept_code in ("2A", "2B"):
        return False
    try:
        return int(dept_code) >= 97
    except ValueError:
        return False


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
        return mapping(s.simplify(tolerance, preserve_topology=True))
    except Exception:
        return geom


def build_detail(type_zone: str, niveau_label: str, debut: str, fin: str, nom: str) -> str:
    """Formate le popup HTML d'une zone."""
    label = TYPE_LABEL.get(type_zone, type_zone)
    parts = [f"<b>{label}</b>"]
    if niveau_label:
        parts.append(f"• {niveau_label}")
    if debut and fin:
        parts.append(f"du {format_date(debut)} au {format_date(fin)}")
    elif debut:
        parts.append(f"depuis le {format_date(debut)}")
    if nom:
        parts.append(f"<br><small>Zone : {nom}</small>")
    return "<br>".join(parts)


def dept_features(depts_geojson: dict) -> list:
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
                "detail": "",
            },
        })
    return features


def zone_features(zones_raw: list) -> list:
    features = []
    for f in zones_raw:
        p = f.get("properties") or {}
        dept = p.get("departement") or {}
        dept_code = dept.get("code", "") if isinstance(dept, dict) else ""

        if dept_code and is_dom_tom(dept_code):
            continue

        niveau = p.get("niveauGravite") or ""
        arrete = p.get("arreteRestriction") or {}
        nom = p.get("nom", "")
        type_zone = p.get("type", "")

        features.append({
            "type": "Feature",
            "geometry": simplify_geometry(f.get("geometry")),
            "properties": {
                "id": p.get("id", ""),
                "nom": nom,
                "departement_code": dept_code,
                "departement_nom": dept.get("nom", "") if isinstance(dept, dict) else "",
                "type_zone": type_zone,
                "niveau": niveau,
                "niveau_label": NIVEAUX.get(niveau, niveau),
                "severity": SEVERITY.get(niveau, "") if niveau else "",
                "arrete_numero": arrete.get("numero", ""),
                "debut": arrete.get("dateDebut", ""),
                "fin": arrete.get("dateFin", ""),
                "detail": build_detail(
                    type_zone,
                    NIVEAUX.get(niveau, niveau),
                    arrete.get("dateDebut", ""),
                    arrete.get("dateFin", ""),
                    nom,
                ),
            },
        })

    # Trier par sévérité croissante : les zones les plus critiques sont rendues en dernier
    # (donc visuellement au-dessus dans Flourish) et leur popup apparaît au survol
    features.sort(key=lambda feat: SEVERITY.get(feat["properties"]["niveau"], 0))
    return features


def build_geojson(dept_feats: list, zone_feats: list) -> dict:
    features = dept_feats + zone_feats
    return {
        "type": "FeatureCollection",
        "generated_at": datetime.date.today().isoformat(),
        "total_features": len(features),
        "features": features,
    }


def save(data: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def main():
    os.makedirs(LATEST_DIR, exist_ok=True)
    os.makedirs(ARCHIVES_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()

    depts_raw = fetch_geojson(DEPTS_URL)
    zones_raw = fetch_geojson(GEOJSON_URL)

    base = dept_features(depts_raw)
    all_zones = zone_features(zones_raw.get("features", []))

    for type_code, type_name in [("SUP", "surface"), ("SOU", "souterrain"), ("AEP", "robinet")]:
        filtered = [f for f in all_zones if f["properties"]["type_zone"] == type_code]
        geojson = build_geojson(base, filtered)

        save(geojson, os.path.join(ARCHIVES_DIR, f"vigieau_{type_name}_{today}.geojson"))
        save(geojson, os.path.join(LATEST_DIR, f"{type_name}.geojson"))

        counts = Counter(f["properties"]["niveau"] for f in filtered)
        print(f"\n── {TYPE_LABEL[type_code]} ({len(filtered)} zones) ──")
        for niveau, label in NIVEAUX.items():
            print(f"  {label:<20} : {counts.get(niveau, 0)}")

    print(f"\nLatest  : data/latest/surface.geojson / souterrain.geojson / robinet.geojson")
    print(f"Archives: data/archives/vigieau_*_{today}.geojson")


if __name__ == "__main__":
    main()
