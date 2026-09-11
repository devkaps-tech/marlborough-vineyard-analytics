"""
Step 10: Build dashboard.html -- a working replica AND a Tableau build spec.

WHY THIS IS GENERATED, NOT HAND-WRITTEN

Every figure on the page is read from the pipeline's own outputs and inlined at
build time. Nothing is transcribed. That is the same rule the rest of the project
follows: if a number appears in two places, one of them will eventually be wrong,
so only the pipeline is allowed to produce them.

The page has two jobs:
  1. Show what the finished dashboard should look like and behave like.
  2. Tell you exactly how to rebuild each worksheet in Tableau Public -- which
     export feeds it, mark type, shelf placements, calculated-field formulas in
     Tableau syntax, and the places Tableau behaves differently from this page.

Reads:  data/tableau/*, data/processed/stats/*, data/processed/*
Writes: dashboard.html (project root)

Usage:
    python3 src/10_build_dashboard.py
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    BASE_DIR,
    MODEL_FITS,
    NEW_PLANTINGS_GEO,
    STATS_DIR,
    TABLEAU_DIR,
    ensure_dirs,
    require,
)

OUT = BASE_DIR / "dashboard.html"

# Map subset for inlining. Heavier detail stays in the Tableau export; this is
# sized so the page stays a few MB rather than tens.
MAP_MIN_HA = 2.0
MAP_SIMPLIFY_M = 30


def load_data() -> dict:
    yearly = pd.read_csv(TABLEAU_DIR / "yearly_metrics.csv")
    trans = pd.read_csv(TABLEAU_DIR / "transitions.csv")
    sub = pd.read_csv(TABLEAU_DIR / "subregion_metrics.csv")
    fits = json.loads(MODEL_FITS.read_text())

    phases = pd.read_csv(STATS_DIR / "growth_phases.csv")
    sizes = pd.read_csv(STATS_DIR / "size_distribution.csv")

    classification = STATS_DIR / "retirement_classification.csv"
    bands = pd.read_csv(classification) if classification.exists() else None

    # --- headline figures, all derived --------------------------------------
    first, last = yearly.iloc[0], yearly.iloc[-1]
    kpi = {
        "footprint_2025": float(last["footprint_ha"]),
        "footprint_2000": float(first["footprint_ha"]),
        "growth_x": float(last["footprint_ha"] / first["footprint_ha"]),
        "gross_new": float(trans["new_ha"].sum()),
        "gross_retired": float(trans["retired_ha"].sum()),
        "net_change": float(trans["net_change_ha"].sum()),
        "churn_ratio": float(trans["new_ha"].sum() / trans["net_change_ha"].sum()),
        "n_surveys": int(len(yearly)),
        "n_parcels_2025": int(last["total_parcels"]) if "total_parcels" in yearly else None,
    }
    if bands is not None:
        kpi["genuine_removal"] = float(bands["likely_genuine_removal"].sum())
        kpi["measurement_change"] = float(bands["likely_measurement_change"].sum())
        kpi["ambiguous"] = float(bands["ambiguous"].sum())

    primary = {k: v for k, v in fits.get("primary", {}).items() if isinstance(v, dict)}
    kpi["k_identified"] = bool(fits.get("headline", {}).get("k_identified"))
    kpi["k_estimates"] = {
        name: {
            "K": f.get("K_ha"),
            "ci": f.get("K_ci95_ha") or f.get("K_ci95_withheld", {}).get("computed"),
        }
        for name, f in primary.items() if f.get("converged")
    }

    return {
        "kpi": kpi,
        "yearly": yearly.where(pd.notna(yearly), None).to_dict("records"),
        "transitions": trans.where(pd.notna(trans), None).to_dict("records"),
        "subregion": sub.where(pd.notna(sub), None).to_dict("records"),
        "phases": phases.where(pd.notna(phases), None).to_dict("records"),
        "sizes": sizes.where(pd.notna(sizes), None).to_dict("records"),
        "bands": (bands.where(pd.notna(bands), None).to_dict("records")
                  if bands is not None else []),
        "fits": {
            "primary": kpi["k_estimates"],
            # Raw parameters so the page can draw the fitted curve itself rather
            # than shipping pre-computed points that could drift from the model.
            "params": {name: f.get("params", {}) for name, f in primary.items()
                       if f.get("converged")},
            "time_origin": fits.get("meta", {}).get("time_origin", 2000),
            "n_primary": fits.get("meta", {}).get("n_observations_primary"),
            "comparison": fits.get("model_comparison", {}),
            "do_not_claim": fits.get("headline", {}).get("do_not_claim", []),
        },
    }


def load_map() -> dict:
    """New-planting polygons, reduced enough to inline without bloating the page."""
    g = gpd.read_parquet(NEW_PLANTINGS_GEO)
    g = g[g["piece_ha"] >= MAP_MIN_HA].copy()
    g["geometry"] = g.geometry.simplify(MAP_SIMPLIFY_M)
    g = g[~g.geometry.is_empty & g.geometry.notna()]
    g = g.to_crs(epsg=4326)

    feats = []
    for row in g.itertuples():
        geom = row.geometry
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        rings = [[[round(x, 5), round(y, 5)] for x, y in p.exterior.coords]
                 for p in polys]
        feats.append({
            "i": row.interval,
            "y": int(row.survey_year),
            "s": row.subregion,
            "ha": round(float(row.piece_ha), 2),
            "r": rings,
        })
    bounds = g.total_bounds
    return {"features": feats,
            "bounds": [round(float(b), 5) for b in bounds],
            "min_ha": MAP_MIN_HA,
            "simplify_m": MAP_SIMPLIFY_M,
            "n": len(feats)}


def build_html(data: dict, mapdata: dict) -> str:
    payload = json.dumps({"data": data, "map": mapdata}, separators=(",", ":"))
    return TEMPLATE.replace("__PAYLOAD__", payload)


# The page template lives at the bottom so the data plumbing above reads first.
TEMPLATE = (Path(__file__).resolve().parent / "dashboard_template.html").read_text()


def main():
    ensure_dirs()
    require(TABLEAU_DIR / "yearly_metrics.csv", TABLEAU_DIR / "transitions.csv",
            MODEL_FITS, NEW_PLANTINGS_GEO)

    data = load_data()
    mapdata = load_map()
    html = build_html(data, mapdata)
    OUT.write_text(html)

    print(f"Wrote {OUT.name}  {OUT.stat().st_size / 1024**2:.2f} MB")
    print(f"  {mapdata['n']} map polygons (>= {MAP_MIN_HA} ha, {MAP_SIMPLIFY_M} m simplify)")
    print(f"  {len(data['yearly'])} survey years, {len(data['transitions'])} intervals")
    print(f"  carrying capacity identified: {data['kpi']['k_identified']}")


if __name__ == "__main__":
    main()
