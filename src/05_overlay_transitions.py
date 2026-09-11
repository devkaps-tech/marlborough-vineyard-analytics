"""
Step 5: Spatial overlay between consecutive surveys -- the analytical core.

WHY THIS EXISTS
The naive `yearly_summary.total_area_ha` is the SUM of per-parcel
`AreaHectares`. That has two defects which make it unsafe to present:

  1. It double-counts wherever polygons overlap within a single survey year.
  2. Its year-on-year difference conflates *genuinely new plantings* with the
     *same land re-digitised at finer resolution*. This is why mean parcel size
     appears to fall from 27 ha to 6.5 ha across the series.

The source has no persistent parcel ID, so the change cannot be tracked
per-parcel. But the geometry is there every year, so set operations on the
dissolved footprints recover the real quantities:

    new_ha        = A(t1) \\ A(t0)     land planted since the last survey
    retired_ha    = A(t0) \\ A(t1)     land that left production
    persisting_ha = A(t0) n A(t1)     land planted in both

All areas are computed in EPSG:2193 (NZTM2000) so they are true metres.

Reads:  parcels_enriched.parquet
Writes: footprint_by_year.csv, transitions.csv, transitions_by_subregion.csv,
        new_plantings.parquet

Usage:
    python3 src/05_overlay_transitions.py
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import make_valid, union_all
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    CRS_NZTM,
    FOOTPRINT_BY_YEAR,
    NEW_PLANTINGS_GEO,
    PARCELS_ENRICHED,
    TRANSITIONS,
    TRANSITIONS_SUBREGION,
    ensure_dirs,
    require,
)

M2_PER_HA = 10_000.0
# Stands in for a sub-region's footprint in years where it has no parcels.
EMPTY_GEOM = Polygon()
# Area-conservation assertions are checked to 0.01 ha (100 m^2) absolute.
# Floating-point set operations on thousands of polygons will not close exactly.
AREA_TOL_HA = 0.01


def ha(geom) -> float:
    return 0.0 if geom is None or geom.is_empty else geom.area / M2_PER_HA


def dissolve_footprints(gdf: gpd.GeoDataFrame, keys: list[str]) -> dict:
    """
    Union all parcels within each group into one geometry.

    Dissolving first is what makes the set algebra valid: it removes intra-year
    double counting, so `new` and `retired` measure ground, not polygon records.

    Note: pandas returns 1-tuples when grouping by a single-element list, so
    single-key groups are unwrapped to a scalar for a natural dict lookup.
    """
    single = len(keys) == 1
    out = {}
    for key, grp in gdf.groupby(keys, sort=True):
        if single and isinstance(key, tuple):
            key = key[0]
        out[key] = make_valid(union_all(grp.geometry.to_numpy()))
    return out


def transitions_for(footprints: dict, years: list[int], label: str | None = None):
    """Set-difference each consecutive pair of footprints."""
    rows, new_geoms = [], []
    for prior, current in zip(years, years[1:]):
        a0, a1 = footprints[prior], footprints[current]

        new = make_valid(a1.difference(a0))
        retired = make_valid(a0.difference(a1))
        persisting = make_valid(a0.intersection(a1))

        new_ha, retired_ha, persisting_ha = ha(new), ha(retired), ha(persisting)
        fp0, fp1 = ha(a0), ha(a1)
        span = current - prior

        # Area conservation: the pieces must reassemble into the footprints.
        # If these fail, the dissolve or the CRS is wrong -- not a rounding issue.
        assert abs((persisting_ha + new_ha) - fp1) < AREA_TOL_HA, (
            f"{label or 'total'} {prior}->{current}: persisting+new="
            f"{persisting_ha + new_ha:.4f} != footprint({current})={fp1:.4f}"
        )
        assert abs((persisting_ha + retired_ha) - fp0) < AREA_TOL_HA, (
            f"{label or 'total'} {prior}->{current}: persisting+retired="
            f"{persisting_ha + retired_ha:.4f} != footprint({prior})={fp0:.4f}"
        )

        row = {
            "prior_survey_year": prior,
            "survey_year": current,
            "years_in_interval": span,
            "footprint_prior_ha": fp0,
            "footprint_ha": fp1,
            "new_ha": new_ha,
            "retired_ha": retired_ha,
            "persisting_ha": persisting_ha,
            "net_change_ha": new_ha - retired_ha,
            "new_ha_per_year": new_ha / span,
            "retired_ha_per_year": retired_ha / span,
            "net_change_ha_per_year": (new_ha - retired_ha) / span,
            "annualised_pct_growth": ((fp1 / fp0) ** (1 / span) - 1) * 100 if fp0 else None,
            # How much of the naive reported change is NOT genuinely new land.
            # Positive means the naive series overstates new planting.
            "naive_net_change_ha": fp1 - fp0,
        }
        if label is not None:
            row["subregion"] = label
        rows.append(row)

        if not new.is_empty:
            new_geoms.append({
                "prior_survey_year": prior,
                "survey_year": current,
                "interval": f"{prior}-{current}",
                "subregion": label,
                "new_ha": new_ha,
                "geometry": new,
            })

    return pd.DataFrame(rows), new_geoms


def main():
    ensure_dirs()
    require(PARCELS_ENRICHED)

    gdf = gpd.read_parquet(PARCELS_ENRICHED).to_crs(epsg=CRS_NZTM)
    gdf["geometry"] = gdf.geometry.apply(make_valid)
    years = sorted(gdf["survey_year"].unique().tolist())
    print(f"Loaded {len(gdf)} parcels across {len(years)} survey years, CRS={gdf.crs}")

    # --- dissolved footprint per year vs the naive sum ---------------------
    print("\nDissolving annual footprints...")
    footprints = dissolve_footprints(gdf, ["survey_year"])

    naive = gdf.groupby("survey_year")["area_hectares"].sum()
    footprint_rows = []
    for y in years:
        fp = ha(footprints[y])
        nv = float(naive[y])
        footprint_rows.append({
            "survey_year": y,
            "footprint_ha": fp,
            "naive_sum_ha": nv,
            "overlap_ha": nv - fp,
            "overlap_pct": (nv - fp) / nv * 100 if nv else 0.0,
        })
    footprint = pd.DataFrame(footprint_rows)

    # The dissolved footprint can never exceed the sum of the parts. If it
    # does, the dissolve or the projection is broken.
    bad = footprint[footprint["footprint_ha"] > footprint["naive_sum_ha"] + AREA_TOL_HA]
    if not bad.empty:
        raise AssertionError(
            f"Dissolved footprint exceeds naive sum for year(s) "
            f"{bad['survey_year'].tolist()} -- dissolve or CRS is wrong."
        )

    footprint.to_csv(FOOTPRINT_BY_YEAR, index=False)
    print(footprint.to_string(index=False))

    # --- transitions, total -----------------------------------------------
    print("\nComputing year-on-year transitions...")
    trans, new_geoms = transitions_for(footprints, years)
    trans.to_csv(TRANSITIONS, index=False)
    print(trans[["prior_survey_year", "survey_year", "years_in_interval", "new_ha",
                 "retired_ha", "net_change_ha", "naive_net_change_ha",
                 "new_ha_per_year"]].to_string(index=False))

    # --- transitions, per sub-region --------------------------------------
    print("\nComputing transitions per sub-region...")
    sub_footprints = dissolve_footprints(gdf, ["subregion", "survey_year"])
    sub_frames, sub_geoms = [], []
    for sub in sorted(gdf["subregion"].unique()):
        # Every sub-region is differenced across the FULL year list, with an
        # empty footprint standing in for years where it has no parcels.
        #
        # Restricting to the years a sub-region actually appears in would drop
        # its first appearance entirely -- there'd be no prior footprint to
        # difference against, so the land would never be counted as new. That
        # silently lost 684 ha for Awatere in 2002 and 104 ha for the minor
        # pockets in 2005, and made the per-region series fail to sum to the
        # national one. Differencing against empty attributes a first
        # appearance wholly to new land, which is what actually happened.
        fp = {y: sub_footprints.get((sub, y), EMPTY_GEOM) for y in years}
        frame, geoms = transitions_for(fp, years, label=sub)
        sub_frames.append(frame)
        sub_geoms.extend(geoms)

    pd.concat(sub_frames, ignore_index=True).to_csv(TRANSITIONS_SUBREGION, index=False)

    # --- new-planting polygons for the dashboard --------------------------
    # Per-subregion pieces are kept (not the totals) so the map layer can be
    # filtered by region without double-drawing the same ground.
    new_gdf = gpd.GeoDataFrame(sub_geoms, crs=f"EPSG:{CRS_NZTM}").explode(
        index_parts=False, ignore_index=True
    )
    new_gdf = new_gdf[new_gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    new_gdf["piece_ha"] = new_gdf.geometry.area / M2_PER_HA
    new_gdf.to_parquet(NEW_PLANTINGS_GEO)

    print(f"\nAll area-conservation assertions passed.")
    print(f"Wrote {FOOTPRINT_BY_YEAR.name}, {TRANSITIONS.name}, "
          f"{TRANSITIONS_SUBREGION.name}, {NEW_PLANTINGS_GEO.name} "
          f"({len(new_gdf)} new-planting polygons)")


if __name__ == "__main__":
    main()
