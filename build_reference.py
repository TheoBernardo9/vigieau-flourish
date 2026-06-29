#!/usr/bin/env python3
"""
Script one-shot : construit reference_zones.geojson avec TOUS les contours de zones
en fusionnant les archives historiques 2019-2024 + l'année en cours.
À lancer une fois, puis uploader dans Flourish comme boundaries fixes.
Jointure quotidienne via l'id de zone.
"""

import json
import urllib.request
import zipfile
import subprocess
import tempfile
import io
import os
import glob
from shapely.geometry import shape, mapping
from shapely.validation import make_valid
from shapely.geometry import MultiPolygon, GeometryCollection

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT = os.path.join(DATA_DIR, "reference_zones.geojson")

CURRENT_URL = "https://regleau.s3.gra.perf.cloud.ovh.net/geojson/zones_arretes_en_vigueur.geojson"
HISTORICAL_URLS = [
    "https://regleau.s3.gra.perf.cloud.ovh.net/geojson/zones_geojson_2024.zip",
    "https://regleau.s3.gra.perf.cloud.ovh.net/geojson/zones_geojson_2023.zip",
    "https://regleau.s3.gra.perf.cloud.ovh.net/geojson/zones_geojson_2022.zip",
    "https://regleau.s3.gra.perf.cloud.ovh.net/geojson/zones_geojson_2021.zip",
    "https://regleau.s3.gra.perf.cloud.ovh.net/geojson/zones_geojson_2020.zip",
    "https://regleau.s3.gra.perf.cloud.ovh.net/geojson/zones_geojson_2019.zip",
]

MOIS = ["jan.", "fév.", "mars", "avr.", "mai", "juin",
        "juil.", "août", "sept.", "oct.", "nov.", "déc."]

TYPE_LABEL = {"SOU": "Eaux souterraines", "SUP": "Eaux de surface", "AEP": "Eau du robinet"}


def is_dom_tom(code: str) -> bool:
    if code in ("2A", "2B"):
        return False
    try:
        return int(code) >= 97
    except ValueError:
        return False


def fetch_bytes(url: str) -> bytes:
    print(f"  Téléchargement {url.split('/')[-1]}…")
    req = urllib.request.Request(url, headers={"User-Agent": "vigieau-flourish/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def simplify_geometry(geom):
    if not geom:
        return None
    try:
        s = make_valid(shape(geom))
        s = s.simplify(0.001, preserve_topology=True)
        if isinstance(s, GeometryCollection) and not isinstance(s, MultiPolygon):
            polys = [g for g in s.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
            if not polys:
                return None
            s = MultiPolygon(polys) if len(polys) > 1 else polys[0]
        return mapping(s)
    except Exception:
        return None


def extract_zones(features: list, seen: dict):
    """Ajoute dans seen {id → feature} les zones pas encore connues."""
    added = 0
    for f in features:
        p = f.get("properties") or {}
        fid = p.get("id") or p.get("idSandre")
        if not fid or fid in seen:
            continue
        dept = p.get("departement") or {}
        dept_code = dept.get("code", "") if isinstance(dept, dict) else str(dept)
        if dept_code and is_dom_tom(dept_code):
            continue
        geom = simplify_geometry(f.get("geometry"))
        if not geom:
            continue
        seen[fid] = {
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "id": fid,
                "nom": p.get("nom", ""),
                "departement_code": dept_code,
                "departement_nom": dept.get("nom", "") if isinstance(dept, dict) else "",
                "type_zone": p.get("type", ""),
                "type_zone_label": TYPE_LABEL.get(p.get("type", ""), p.get("type", "")),
            },
        }
        added += 1
    return added


def load_zip(data: bytes) -> list:
    """Extrait tous les GeoJSON d'un ZIP (gère ZIP64 et compressions non standard via unzip)."""
    features = []
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "archive.zip")
        with open(zip_path, "wb") as f:
            f.write(data)
        # Utilise unzip système pour gérer ZIP64 / LZMA / etc.
        result = subprocess.run(
            ["unzip", "-q", zip_path, "-d", tmpdir],
            capture_output=True
        )
        for geojson_path in glob.glob(os.path.join(tmpdir, "**", "*.geojson"), recursive=True) + \
                            glob.glob(os.path.join(tmpdir, "**", "*.json"), recursive=True):
            try:
                with open(geojson_path, encoding="utf-8") as f:
                    gj = json.load(f)
                    features += gj.get("features", [])
            except Exception:
                pass
    return features


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    seen = {}

    # Zones actives (année en cours)
    print("Année en cours :")
    raw = json.loads(fetch_bytes(CURRENT_URL))
    n = extract_zones(raw.get("features", []), seen)
    print(f"  → {n} nouvelles zones ({len(seen)} total)")

    # Archives historiques
    print("\nArchives historiques :")
    for url in HISTORICAL_URLS:
        try:
            data = fetch_bytes(url)
            features = load_zip(data)
            n = extract_zones(features, seen)
            print(f"  → {n} nouvelles zones ({len(seen)} total)")
        except Exception as e:
            print(f"  ✗ Erreur {url.split('/')[-1]} : {e}")

    # Export
    geojson = {
        "type": "FeatureCollection",
        "total_zones": len(seen),
        "features": list(seen.values()),
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(OUTPUT) / 1_000_000
    print(f"\n✓ {OUTPUT}")
    print(f"  {len(seen)} zones uniques — {size_mb:.1f} MB")
    print(f"\nÀ uploader UNE FOIS dans Flourish comme boundaries.")
    print(f"Clé de jointure : id")


if __name__ == "__main__":
    main()
