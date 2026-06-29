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
from collections import Counter, defaultdict
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


def build_arrete_details(zones_raw: list) -> dict:
    """
    Construit un dict {arrete_id → detail_text} en regroupant
    les couches SOU/SUP/AEP d'un même arrêté préfectoral.
    """
    # Groupe par arrêté ID → liste de (type_zone, niveauGravite, dateDebut, dateFin)
    groups = defaultdict(dict)  # arrete_id → {type: (niveau, debut, fin, numero)}

    for f in zones_raw:
        p = f.get("properties") or {}
        dept = p.get("departement") or {}
        dept_code = dept.get("code", "") if isinstance(dept, dict) else ""
        if dept_code and is_dom_tom(dept_code):
            continue

        arrete = p.get("arreteRestriction") or {}
        arrete_id = arrete.get("id")
        if not arrete_id:
            continue

        type_zone = p.get("type", "")
        groups[arrete_id][type_zone] = {
            "niveau": p.get("niveauGravite") or "",
            "debut": arrete.get("dateDebut") or "",
            "fin": arrete.get("dateFin") or "",
            "numero": arrete.get("numero") or "",
        }

    # Pour chaque arrêté, construire le texte du popup
    details = {}
    for arrete_id, types in groups.items():
        lines = []
        for t in TYPE_ORDER:
            if t not in types:
                continue
            info = types[t]
            niveau_label = NIVEAUX.get(info["niveau"], info["niveau"])
            label = TYPE_LABEL.get(t, t)
            lines.append(label)
            if niveau_label:
                lines.append(f"• {niveau_label}")
            if info["debut"] and info["fin"]:
                lines.append(f"du {format_date(info['debut'])} au {format_date(info['fin'])}")
            elif info["debut"]:
                lines.append(f"depuis le {format_date(info['debut'])}")
        details[arrete_id] = "\n".join(lines)

    return details


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


def zone_features(zones_raw: list, arrete_details: dict) -> list:
    features = []
    for f in zones_raw:
        p = f.get("properties") or {}
        dept = p.get("departement") or {}
        dept_code = dept.get("code", "") if isinstance(dept, dict) else ""

        if dept_code and is_dom_tom(dept_code):
            continue

        niveau = p.get("niveauGravite") or ""
        arrete = p.get("arreteRestriction") or {}
        arrete_id = arrete.get("id")

        features.append({
            "type": "Feature",
            "geometry": simplify_geometry(f.get("geometry")),
            "properties": {
                "id": p.get("id", ""),
                "nom": p.get("nom", ""),
                "departement_code": dept_code,
                "departement_nom": dept.get("nom", "") if isinstance(dept, dict) else "",
                "type_zone": p.get("type", ""),
                "niveau": niveau,
                "niveau_label": NIVEAUX.get(niveau, niveau),
                "severity": SEVERITY.get(niveau, 0) if niveau else "",
                "arrete_numero": arrete.get("numero", ""),
                "debut": arrete.get("dateDebut", ""),
                "fin": arrete.get("dateFin", ""),
                "detail": arrete_details.get(arrete_id, ""),
            },
        })
    return features


def build_flourish_geojson(depts: dict, zones: dict) -> dict:
    raw = zones.get("features", [])
    arrete_details = build_arrete_details(raw)

    features = dept_features(depts)
    features += zone_features(raw, arrete_details)

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

    counts = Counter(f["properties"]["niveau"] for f in flourish["features"])
    print("\nRépartition :")
    print(f"  Départements base   : {sum(1 for f in flourish['features'] if f['properties']['type_zone'] == 'departement')}")
    for niveau, label in NIVEAUX.items():
        print(f"  {label:<20} : {counts.get(niveau, 0)}")


if __name__ == "__main__":
    main()
