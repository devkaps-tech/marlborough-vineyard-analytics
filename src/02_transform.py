"""
Step 2: Clean and transform the raw vineyard parcel data.

- Repairs invalid geometries
- Reprojects to EPSG:4326 (WGS84) so downstream tools (Tableau, PostGIS,
  web maps) all agree on a standard, interoperable CRS
- Builds a clean, snake_case schema
- Adds a per-snapshot surrogate parcel_id (the source has NO persistent parcel
  ID across years -- see data/README.md)
- Computes the naive yearly_summary rollup

NOTE on `total_area_ha` in the summary: it is the SUM of per-parcel
`AreaHectares`, which double-counts wherever polygons overlap within a year.
Step 05 computes the dissolved footprint, which is the honest area measure.
This naive series is kept deliberately -- the gap between the two is a finding.

Writes: parcels_clean.parquet, yearly_summary.csv

Usage:
    python3 src/02_transform.py
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    CRS_WGS84,
    GDB_LAYER,
    PARCELS_CLEAN,
    YEARLY_SUMMARY,
    ensure_dirs,
    find_gdb,
)


def load_raw() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(find_gdb(), layer=GDB_LAYER)
    gdf = gdf.rename(columns={
        "year": "survey_year",
        "coverageye": "survey_date",
        "AreaHectares": "area_hectares",
    })
    return gdf[["survey_year", "survey_date", "area_hectares", "geometry"]]


def clean(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()

    # fix invalid geometries (self-intersections etc.) rather than dropping them
    n_invalid = (~gdf.geometry.is_valid).sum()
    gdf["geometry"] = gdf.geometry.buffer(0)
    print(f"Repaired {n_invalid} invalid geometries via buffer(0)")

    # drop anything still empty/invalid after repair, or that isn't polygonal
    # at all (buffer(0) can degrade a bad ring to a line or point)
    before = len(gdf)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if len(gdf) < before:
        print(f"Dropped {before - len(gdf)} rows with unrepairable/non-polygonal geometry")

    # buffer(0) can also collapse a MultiPolygon down to a single Polygon when
    # only one ring survives. The PostGIS column in sql/schema.sql is
    # fixed-typed to MultiPolygon, so normalize every row to match -- otherwise
    # psycopg2 rejects the mismatched rows on load. Don't remove this without
    # also loosening that column type.
    n_singles = (gdf.geometry.geom_type == "Polygon").sum()
    gdf["geometry"] = gdf.geometry.apply(
        lambda g: MultiPolygon([g]) if g.geom_type == "Polygon" else g
    )
    print(f"Normalized {n_singles} single Polygon(s) to MultiPolygon for schema consistency")

    gdf["survey_year"] = gdf["survey_year"].astype(int)

    # surrogate key: unique within a survey year, NOT a stable parcel identity
    # across years (the source has no such linkage)
    gdf["parcel_id"] = (
        gdf["survey_year"].astype(str) + "-" +
        gdf.groupby("survey_year").cumcount().astype(str)
    )

    gdf = gdf.to_crs(epsg=CRS_WGS84)

    return gdf[["parcel_id", "survey_year", "survey_date", "area_hectares", "geometry"]]


def build_yearly_summary(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    summary = (
        gdf.groupby("survey_year")
        .agg(total_parcels=("parcel_id", "count"),
             total_area_ha=("area_hectares", "sum"),
             avg_parcel_size_ha=("area_hectares", "mean"))
        .reset_index()
        .sort_values("survey_year")
    )
    summary["prior_survey_year"] = summary["survey_year"].shift(1)
    summary["years_since_prior_survey"] = summary["survey_year"] - summary["prior_survey_year"]
    summary["area_added_ha"] = summary["total_area_ha"].diff()
    summary["pct_growth"] = summary["total_area_ha"].pct_change() * 100

    # Survey intervals are irregular (2013->2018, 2018->2020), so raw
    # pct_growth is NOT comparable between rows. Always compare annualised.
    summary["annualised_pct_growth"] = (
        (summary["total_area_ha"] / summary["total_area_ha"].shift(1))
        ** (1 / summary["years_since_prior_survey"]) - 1
    ) * 100
    return summary


def main():
    ensure_dirs()

    raw = load_raw()
    print(f"Loaded {len(raw)} raw parcels")

    clean_gdf = clean(raw)
    print(f"Clean dataset: {len(clean_gdf)} parcels, CRS={clean_gdf.crs}")

    summary = build_yearly_summary(clean_gdf)

    clean_gdf.to_parquet(PARCELS_CLEAN)
    summary.to_csv(YEARLY_SUMMARY, index=False)

    print("\nYearly summary (naive sum-of-parcels -- see step 05 for the dissolved footprint):")
    print(summary.to_string(index=False))
    print(f"\nWrote {PARCELS_CLEAN.name} and {YEARLY_SUMMARY.name}")


if __name__ == "__main__":
    main()
