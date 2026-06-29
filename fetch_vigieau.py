#!/usr/bin/env python3
"""
Récupère le GeoJSON vigieau (zones de restriction sécheresse) et le prépare pour Flourish.
- Départements France métropole + Corse en base (sans restriction)
- Zones avec arrêtés actifs par-dessus (severity 1-4)
- Popup detail avec les 3 couches (SOU / SUP / AEP) regroupées par arrêté
"""

import json
import csv
import urllib.request
import datetime
import os
from collections import Counter
from shapely.geometry import shape, mapping, MultiPolygon as SMP
from shapely.strtree import STRtree
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

NIVEAU_COLOR = {
    "vigilance":       "#fff9bd",
    "alerte":          "orange",
    "alerte_renforcee": "red",
    "crise":           "maroon",
    "":                "#e6e6e6",
}

NIVEAU_TEXT_COLOR = {
    "vigilance":       "#333",
    "alerte":          "#fff",
    "alerte_renforcee": "#fff",
    "crise":           "#fff",
    "":                "#666",
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
        from shapely.geometry import MultiPolygon, GeometryCollection
        s = make_valid(shape(geom))
        s = s.simplify(tolerance, preserve_topology=True)
        # GeometryCollection → extraire uniquement les polygones
        if isinstance(s, GeometryCollection) and not isinstance(s, MultiPolygon):
            polys = [g for g in s.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
            if not polys:
                return None
            s = MultiPolygon(polys) if len(polys) > 1 else polys[0]
        return mapping(s)
    except Exception:
        return geom


def badge(niveau: str, niveau_label: str) -> str:
    bg = NIVEAU_COLOR.get(niveau, "#e6e6e6")
    color = NIVEAU_TEXT_COLOR.get(niveau, "#333")
    label = niveau_label or "Aucune restriction"
    return (f'<span style="background:{bg};color:{color};padding:2px 7px;'
            f'border-radius:4px;font-weight:bold;font-size:0.85em">{label}</span>')


def build_detail(type_zone: str, niveau: str, niveau_label: str, debut: str, fin: str, nom: str) -> str:
    """Formate le bloc HTML d'une couche pour le popup."""
    label = TYPE_LABEL.get(type_zone, type_zone)
    parts = [f"<b>{label}</b>", badge(niveau, niveau_label)]
    if debut and fin:
        parts.append(f"du {format_date(debut)} au {format_date(fin)}")
    elif debut:
        parts.append(f"depuis le {format_date(debut)}")
    if nom:
        parts.append(f"<small>Zone : {nom}</small>")
    return "<br>".join(parts)


def build_combined_detail(layers: dict) -> str:
    """
    layers = {type_code: {niveau, niveau_label, debut, fin, nom}}
    Construit un popup HTML affichant les 3 couches dans l'ordre SOU/SUP/AEP.
    """
    blocks = []
    for t in TYPE_ORDER:
        if t not in layers:
            continue
        info = layers[t]
        blocks.append(build_detail(t, info["niveau"], info["niveau_label"], info["debut"], info["fin"], info["nom"]))
    return "<br><br>".join(blocks)


def build_spatial_index(features: list):
    """Retourne (STRtree, liste de shapes) pour une liste de features GeoJSON."""
    geoms = []
    for f in features:
        g = f.get("geometry")
        try:
            geoms.append(make_valid(shape(g)) if g else None)
        except Exception:
            geoms.append(None)
    valid = [g for g in geoms if g is not None]
    tree = STRtree(valid)
    return tree, geoms


def find_overlapping(geom, tree, features, geoms):
    """Trouve la feature qui chevauche le plus geom dans un layer donné."""
    if geom is None:
        return None
    candidates = tree.query(geom)
    best = None
    best_area = 0
    for idx in candidates:
        g = geoms[idx]
        if g is None:
            continue
        try:
            inter = geom.intersection(g)
            if inter.is_empty:
                continue
            a = inter.area
            if a > best_area:
                best_area = a
                best = features[idx]
        except Exception:
            continue
    return best


def enrich_combined_detail(all_zones: list) -> list:
    """
    Pour chaque zone du fichier combiné, enrichit le popup
    avec les infos des 3 couches (SUP/SOU/AEP) en faisant une jointure spatiale.
    """
    print("Calcul des jointures spatiales pour les popups combinés…")

    # Séparer par type et construire les shapes
    by_type = {t: [] for t in TYPE_ORDER}
    shapes_by_type = {t: [] for t in TYPE_ORDER}

    for f in all_zones:
        t = f["properties"].get("type_zone")
        if t in by_type:
            by_type[t].append(f)
            g = f.get("geometry")
            try:
                shapes_by_type[t].append(make_valid(shape(g)) if g else None)
            except Exception:
                shapes_by_type[t].append(None)

    # STRtree par type
    trees = {}
    for t in TYPE_ORDER:
        valids = [g for g in shapes_by_type[t] if g is not None]
        trees[t] = STRtree(valids) if valids else None

    enriched = []
    for f in all_zones:
        own_type = f["properties"].get("type_zone")
        own_geom_raw = f.get("geometry")
        try:
            own_geom = make_valid(shape(own_geom_raw)) if own_geom_raw else None
        except Exception:
            own_geom = None

        layers = {}
        for t in TYPE_ORDER:
            if trees[t] is None or own_geom is None:
                continue
            candidates = trees[t].query(own_geom)
            best = None
            best_area = 0
            for idx in candidates:
                g = shapes_by_type[t][idx]
                if g is None:
                    continue
                try:
                    inter = own_geom.intersection(g)
                    if inter.is_empty:
                        continue
                    a = inter.area
                    if a > best_area:
                        best_area = a
                        best = by_type[t][idx]
                except Exception:
                    continue
            if best:
                bp = best["properties"]
                layers[t] = {
                    "niveau": bp.get("niveau", ""),
                    "niveau_label": bp.get("niveau_label", ""),
                    "debut": bp.get("debut", ""),
                    "fin": bp.get("fin", ""),
                    "nom": bp.get("nom", ""),
                }

        new_f = dict(f)
        new_f["properties"] = dict(f["properties"])
        new_f["properties"]["detail"] = build_combined_detail(layers) if layers else f["properties"].get("detail", "")
        enriched.append(new_f)

    print("Jointures spatiales terminées.")
    return enriched


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
                    niveau,
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


def geom_to_wkt(geom: dict) -> str:
    """Convertit une géométrie GeoJSON en WKT pour Flourish."""
    if not geom:
        return ""
    try:
        return shape(geom).wkt
    except Exception:
        return ""


def save_csv(features: list, path: str):
    """Export CSV avec géométrie WKT pour Flourish live data."""
    cols = ["id", "nom", "departement_code", "departement_nom", "type_zone",
            "niveau", "niveau_label", "severity", "debut", "fin", "arrete_numero", "detail", "geometry"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for feat in features:
            if feat["properties"].get("type_zone") != "departement":
                row = dict(feat["properties"])
                row["geometry"] = geom_to_wkt(feat.get("geometry"))
                w.writerow(row)


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

    # Fichier combiné : jointure spatiale pour popup 3 couches
    all_zones_enriched = enrich_combined_detail(all_zones)
    combined = build_geojson(base, all_zones_enriched)
    save(combined, os.path.join(ARCHIVES_DIR, f"vigieau_complet_{today}.geojson"))
    save(combined, os.path.join(LATEST_DIR, "complet.geojson"))
    print(f"\n── Combiné ({len(all_zones)} zones toutes couches) ──")
    counts = Counter(f["properties"]["niveau"] for f in all_zones)
    for niveau, label in NIVEAUX.items():
        print(f"  {label:<20} : {counts.get(niveau, 0)}")

    # CSV live data pour Flourish (sans géométrie, URL stable)
    CSV_DIR = os.path.join(DATA_DIR, "csv")
    os.makedirs(CSV_DIR, exist_ok=True)
    for type_code, type_name in [("SUP", "surface"), ("SOU", "souterrain"), ("AEP", "robinet")]:
        filtered = [f for f in all_zones if f["properties"]["type_zone"] == type_code]
        save_csv(filtered, os.path.join(CSV_DIR, f"{type_name}.csv"))
    save_csv(all_zones_enriched, os.path.join(CSV_DIR, "complet.csv"))
    print(f"\nCSV live : data/csv/surface.csv / souterrain.csv / robinet.csv / complet.csv")

    print(f"\nLatest  : data/latest/surface.geojson / souterrain.geojson / robinet.geojson / complet.geojson")
    print(f"Archives: data/archives/vigieau_*_{today}.geojson")


if __name__ == "__main__":
    main()
