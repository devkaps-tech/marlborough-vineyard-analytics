"""
Step 7: Export Tableau-ready data.

Tableau Public reads files, not live Postgres, so everything the dashboard needs
is materialised here into data/tableau/.

WHY PURPOSE-BUILT LAYERS RATHER THAN ONE GEOMETRY FILE

Simplification alone cannot shrink a full all-years parcel layer to a usable
size -- the cost is per-feature overhead, not vertex density. Measured:

    all 59,099 parcels, 10 m simplify ....... 36 MB   unusable
    dissolved footprint, 18 features ........ 23 MB   unusable
    new plantings >=0.5 ha, 10 m ............ 2.9 MB  fine
    2025 parcels only, 10 m ................. 3.2 MB  fine

Dissolving does not help because Marlborough's vineyards are thousands of
disjoint blocks -- the vertex count survives the union. Tableau Public also
degrades past roughly 10 MB of spatial data. So each layer here answers one
question and carries only the geometry that question needs.

Geometry is simplified in EPSG:2193 (metres, so the tolerance is meaningful)
and written in EPSG:4326, which is what Tableau expects for .geojson.

Reads:  parcels_enriched.parquet, new_plantings.parquet, footprint_by_year.csv,
        transitions.csv, transitions_by_subregion.csv, stats/model_fits.json,
        stats/retirement_classification.csv (optional)
Writes: data/tableau/*.geojson, data/tableau/*.csv

Usage:
    python3 src/07_export_tableau.py
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    CRS_NZTM,
    CRS_WGS84,
    FOOTPRINT_BY_YEAR,
    MODEL_FITS,
    NEW_PLANTINGS_GEO,
    PARCELS_ENRICHED,
    STATS_DIR,
    TABLEAU_DIR,
    TRANSITIONS,
    TRANSITIONS_SUBREGION,
    ensure_dirs,
    require,
)

M2_PER_HA = 10_000.0
SIMPLIFY_M = 10          # metres; measured at ~0.5% area loss, well inside the 1% cap
MIN_PIECE_HA = 0.5       # drops 88% of new-planting features for 2.7% of the area
SNAPSHOT_YEARS = [2000, 2025]


def write_geojson(gdf: gpd.GeoDataFrame, name: str, simplify_m: int = SIMPLIFY_M):
    """Simplify in metres, reproject to 4326, write, and report the real size."""
    out = gdf.to_crs(epsg=CRS_NZTM).copy()
    area_before = out.geometry.area.sum() / M2_PER_HA
    if simplify_m:
        out["geometry"] = out.geometry.simplify(simplify_m)
        out = out[~out.geometry.is_empty & out.geometry.notna()]
    area_after = out.geometry.area.sum() / M2_PER_HA
    loss = abs(area_after - area_before) / area_before * 100 if area_before else 0.0

    path = TABLEAU_DIR / name
    out.to_crs(epsg=CRS_WGS84).to_file(path, driver="GeoJSON")
    mb = path.stat().st_size / 1024**2
    print(f"  {name:28s} {len(out):6d} features  {mb:5.2f} MB  area loss {loss:.2f}%")
    if loss > 1.0:
        print(f"    WARNING: area loss exceeds the 1% budget -- reduce simplify_m")
    return path


def export_spatial():
    require(PARCELS_ENRICHED, NEW_PLANTINGS_GEO)
    print("Spatial layers:")

    # The analytical centrepiece: where growth actually happened, per interval.
    new = gpd.read_parquet(NEW_PLANTINGS_GEO)
    new = new[new["piece_ha"] >= MIN_PIECE_HA].copy()
    new = new[["interval", "prior_survey_year", "survey_year", "subregion",
               "piece_ha", "geometry"]]
    write_geojson(new, "new_plantings.geojson")

    # Snapshot footprints for a before/after pair.
    parcels = gpd.read_parquet(PARCELS_ENRICHED)
    for year in SNAPSHOT_YEARS:
        snap = parcels[parcels["survey_year"] == year][
            ["parcel_id", "survey_year", "area_hectares", "subregion", "geometry"]
        ]
        write_geojson(snap, f"parcels_{year}.geojson")


def export_yearly_metrics():
    """
    One row per survey year, carrying everything the time-series sheets need --
    including the fitted curve values, so the saturation chart is drawable in
    Tableau natively without re-implementing the model there.
    """
    fp = pd.read_csv(FOOTPRINT_BY_YEAR)
    trans = pd.read_csv(TRANSITIONS)

    df = fp.merge(
        trans[["survey_year", "prior_survey_year", "years_in_interval", "new_ha",
               "retired_ha", "persisting_ha", "net_change_ha", "new_ha_per_year",
               "retired_ha_per_year", "annualised_pct_growth"]],
        on="survey_year", how="left",
    )

    base = df["footprint_ha"].iloc[0]
    df["cumulative_growth_x"] = df["footprint_ha"] / base
    df["pct_of_2025"] = df["footprint_ha"] / df["footprint_ha"].iloc[-1] * 100

    # Fitted curve, if the models converged. Flagged so the dashboard cannot
    # present it as a projection -- see the misspecification note below.
    if MODEL_FITS.exists():
        fits = json.loads(MODEL_FITS.read_text())
        import numpy as np

        t = df["survey_year"].to_numpy(dtype=float) - fits["meta"]["time_origin"]
        for name in ("logistic", "gompertz"):
            f = fits.get("primary", {}).get(name, {})
            if not f.get("converged"):
                continue
            p = f["params"]
            if name == "logistic":
                df[f"fit_{name}_ha"] = p["K"] / (1 + np.exp(-p["r"] * (t - p["t_mid"])))
            else:
                df[f"fit_{name}_ha"] = p["K"] * np.exp(-np.exp(-p["b"] * (t - p["t_mid"])))
            df[f"fit_{name}_K_ha"] = p["K"]
            df[f"fit_{name}_valid_as_limit"] = bool(f.get("K_exceeds_observed", False))

    path = TABLEAU_DIR / "yearly_metrics.csv"
    df.to_csv(path, index=False)
    print(f"  {'yearly_metrics.csv':28s} {len(df):6d} rows    "
          f"{path.stat().st_size / 1024:5.1f} KB")
    return df


def export_subregion_metrics():
    sub = pd.read_csv(TRANSITIONS_SUBREGION)
    total = sub.groupby("survey_year")["new_ha"].transform("sum")
    sub["share_of_new_pct"] = (sub["new_ha"] / total * 100).where(total > 0)

    div = STATS_DIR / "subregion_divergence.csv"
    if div.exists():
        extra = pd.read_csv(div)[["survey_year", "subregion", "share_of_new_pct"]]
        sub = sub.drop(columns="share_of_new_pct").merge(
            extra, on=["survey_year", "subregion"], how="left"
        )

    path = TABLEAU_DIR / "subregion_metrics.csv"
    sub.to_csv(path, index=False)
    print(f"  {'subregion_metrics.csv':28s} {len(sub):6d} rows    "
          f"{path.stat().st_size / 1024:5.1f} KB")


def export_transitions():
    """
    Per-interval churn. Carries the forensics banding when it exists, and an
    explicit 'unclassified' marker when it does not -- so a dashboard built
    against this file cannot silently present retired area as vine removal.
    """
    trans = pd.read_csv(TRANSITIONS)
    classification = STATS_DIR / "retirement_classification.csv"

    if classification.exists():
        band = pd.read_csv(classification)
        key = [c for c in ("prior_survey_year", "survey_year") if c in band.columns]
        if key:
            trans = trans.merge(band, on=key, how="left", suffixes=("", "_forensics"))
            trans["retired_classified"] = True
            print("  (merged retirement classification from forensics)")
        else:
            trans["retired_classified"] = False
            print("  WARNING: retirement_classification.csv has no join key; skipped")
    else:
        trans["retired_classified"] = False
        trans["retired_interpretation"] = (
            "UNCLASSIFIED -- genuine vine removal vs re-digitisation boundary "
            "shift not yet separated; do not label as vines removed"
        )
        print("  (no forensics classification yet -- retired area marked UNCLASSIFIED)")

    path = TABLEAU_DIR / "transitions.csv"
    trans.to_csv(path, index=False)
    print(f"  {'transitions.csv':28s} {len(trans):6d} rows    "
          f"{path.stat().st_size / 1024:5.1f} KB")


def write_readme(df: pd.DataFrame):
    """A short data dictionary, so the Tableau build isn't guesswork."""
    fits = json.loads(MODEL_FITS.read_text()) if MODEL_FITS.exists() else {}
    headline = fits.get("headline", {})
    k_ok = headline.get("k_identified", False)

    (TABLEAU_DIR / "README.md").write_text(f"""# Tableau data exports

Generated by `src/07_export_tableau.py`. Regenerate rather than editing by hand.

All tabular files key on `survey_year` (integer) for clean Tableau relationships.
All spatial files are EPSG:4326 GeoJSON, simplified at {SIMPLIFY_M} m in EPSG:2193.

## Files

| File | Grain | Use |
|---|---|---|
| `yearly_metrics.csv` | one row per survey year | time series, KPIs, saturation chart |
| `subregion_metrics.csv` | year x sub-region | Awatere vs Wairau breakdown |
| `transitions.csv` | one row per interval | gross new / retired / net churn |
| `new_plantings.geojson` | polygons >= {MIN_PIECE_HA} ha | map: where growth happened |
| `parcels_2000.geojson` | 2000 parcels | baseline footprint |
| `parcels_2025.geojson` | 2025 parcels | current footprint |

## Read this before building a chart

**`retired_ha` is NOT confirmed vine removal.** It is the area present in one
survey and absent in the next. Some of it is genuine removal; some is the same
ground re-digitised at a different boundary. Check the `retired_classified`
column: where it is `False`, label the measure "retired (unclassified)" and do
not describe it as vines pulled out.

**Carrying capacity is {'identified' if k_ok else 'NOT identified — do not plot K as a limit'}.**
{'' if k_ok else (
"The post-2005 fits put K above the observed footprint, but their 95% intervals "
"run past 240,000 ha, so the data cannot distinguish 'approaching a limit' from "
"'still growing freely'. The `fit_*_ha` columns are included for a fitted-line "
"overlay only. Do not build a 'years to capacity' KPI, a projection, or a "
"reference line from them.")}

**Survey intervals are irregular** (gaps 2013-2018, 2018-2020). Use
`annualised_pct_growth`, never raw period growth, in any cross-period comparison.

**`subregion` is algorithmically derived** (DBSCAN on parcel centroids), not an
official viticultural boundary, and cannot separate Wairau Valley proper from
the Southern Valleys. Caption any regional view accordingly.

**Mean parcel size falls 27 -> 6.5 ha** across the series. That is digitisation
resolution, not fragmentation. Do not chart it as a trend.
""")
    print(f"  {'README.md':28s} (data dictionary)")


def main():
    ensure_dirs()
    require(FOOTPRINT_BY_YEAR, TRANSITIONS, TRANSITIONS_SUBREGION)
    TABLEAU_DIR.mkdir(parents=True, exist_ok=True)

    export_spatial()
    print("\nTabular:")
    df = export_yearly_metrics()
    export_subregion_metrics()
    export_transitions()
    write_readme(df)

    total_mb = sum(f.stat().st_size for f in TABLEAU_DIR.glob("*")) / 1024**2
    print(f"\nWrote {len(list(TABLEAU_DIR.glob('*')))} files to {TABLEAU_DIR} "
          f"({total_mb:.1f} MB total)")


if __name__ == "__main__":
    main()
