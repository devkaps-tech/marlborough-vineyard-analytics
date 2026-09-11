"""
Step 9: Retirement forensics -- how much of the "retired" 7,999 ha is real
vine removal versus the same ground re-digitised at a different boundary.

WHY THIS EXISTS
`05_overlay_transitions.py` labels A(t0) \\ A(t1) as retired. That is correct
set algebra, but it cannot distinguish a vine that was pulled from a survey
that simply redrew a block's edge -- both remove area from A(t0) without it
reappearing in A(t1). A sliver check already ran on the NEW side (90% of new
pieces are under 1 ha but carry only 4.1% of area), but a whole-hectare
boundary NUDGE does not produce a sliver: a straight edge moved 20 m along a
500 m frontage is a long, fairly compact strip, not a shard. Piece-area and
compactness checks alone would wave that strip through. Closing the gap needs
a test that does not depend on the piece being small.

THE KEY TEST: adjacency to the surviving footprint
Dissolving a survey year merges every parcel that touches another parcel
into one seamless polygon -- so A(t0) has NO internal edge between a block
that will persist and its neighbour that will retire; edges of A(t0) only
exist at the true outer perimeter of the vineyard estate (or real gaps
between blocks) in that year. A boundary-redigitisation piece is, by
construction, a thin rim hugging one of A(t0)'s edges: erode A(t0) inward by
d metres and the rim vanishes completely, because nothing in it is farther
than d metres from an edge. A genuine standalone block that was pulled, by
contrast, sat in the *interior* of A(t0) while still planted (surrounded by
other then-planted land), so most of its area survives the same erosion.

    core_survival_frac(d) = ha(piece  intersection  erode(A(t0), d)) / ha(piece)

This is the primary discriminator. Polsby-Popper compactness is a secondary
diagnostic (slivers score low on both axes; a boundary-shift strip can still
be fairly compact, which is exactly why compactness alone under-detects it).
Reappearance of the same ground in the NEXT survey is a sanity check on the
threshold, per the brief -- not used to define it.

CAVEAT (stated, not resolved): a genuinely removed block sitting at the true
*outer* edge of the estate (not an internal seam) looks identical to a
boundary shift if it is narrower than the buffer distance. This method
cannot tell those apart; it is conservative in the sense that it will
under-count genuine removal for edge-hugging blocks. See the report.

Reads:  parcels_enriched.parquet, transitions.csv (conservation check only)
Writes: stats/retired_pieces.parquet   (cached geometry + features; computed
                                         once, then only reloaded)
        stats/retirement_classification.csv

Usage:
    python3 src/09_retirement_forensics.py
    rm data/processed/stats/retired_pieces.parquet   # to force a full recompute
"""
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import get_parts, make_valid, prepare, union_all

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    CRS_NZTM,
    PARCELS_ENRICHED,
    RETIRED_PIECES_GEO,
    RETIREMENT_CLASSIFICATION,
    TRANSITIONS,
    ensure_dirs,
    require,
)

M2_PER_HA = 10_000.0
AREA_TOL_HA = 0.01          # matches the tolerance used in 05_overlay_transitions.py
MIN_PIECE_HA = 1e-6         # drops floating-point dust from repeated set ops

# Buffer distances (m) tested for the erosion/adjacency signal. 10 m is the
# headline -- roughly the positional tolerance of hand-digitised block
# outlines against orthophotography. 5/20/40 are carried through to show how
# much the split moves if that assumption is wrong.
BUFFERS = [5, 10, 20, 40]
HEADLINE_BUFFER = 10

# Classification bands on core_survival_frac(HEADLINE_BUFFER).
MEASUREMENT_MAX = 0.20      # below this, the piece is essentially all fringe
GENUINE_MIN = 0.60          # above this, most of the piece is deep interior
BAND_ORDER = ["likely_genuine_removal", "ambiguous", "likely_measurement_change"]


def ha(geom) -> float:
    return 0.0 if geom is None or geom.is_empty else geom.area / M2_PER_HA


def dissolve_footprints(gdf: gpd.GeoDataFrame) -> dict:
    return {
        year: make_valid(union_all(grp.geometry.to_numpy()))
        for year, grp in gdf.groupby("survey_year", sort=True)
    }


def polsby_popper(geom) -> float:
    perim = geom.length
    return 4 * np.pi * geom.area / (perim ** 2) if perim > 0 else 0.0


def classify(frac) -> str:
    if pd.isna(frac):
        return "ambiguous"
    if frac < MEASUREMENT_MAX:
        return "likely_measurement_change"
    if frac >= GENUINE_MIN:
        return "likely_genuine_removal"
    return "ambiguous"


def survival_frac(piece, prepared_target, area_ha: float) -> float:
    """
    ha(piece n prepared_target) / area_ha, short-circuited with the prepared
    geometry's fast predicates. A negative-buffer erosion of a large,
    complex multipolygon makes the naive `piece.intersection(eroded)` cost
    ~30 ms/call (measured on the 2011-12 interval, 13,875 pieces); testing
    `intersects`/`contains` first against a *prepared* geometry answers ~90%
    of pieces (fully in or fully out) without a constructive overlay at all,
    cutting the 2011-12 interval from ~42 s to ~1.4 s per buffer distance.
    """
    if area_ha <= 0:
        return 0.0
    if not prepared_target.intersects(piece):
        return 0.0
    if prepared_target.contains(piece):
        return 1.0
    return ha(piece.intersection(prepared_target)) / area_ha


def pieces_for_interval(prior, current, next_year, footprints):
    """Explode A(prior) \\ A(current) into parts and score each one."""
    a0, a1 = footprints[prior], footprints[current]
    retired = make_valid(a0.difference(a1))
    if retired.is_empty:
        return []

    parts = [retired] if retired.geom_type == "Polygon" else list(get_parts(retired))

    eroded = {}
    for d in BUFFERS:
        e = make_valid(a0.buffer(-d))
        prepare(e)
        eroded[d] = e

    later = footprints.get(next_year) if next_year is not None else None
    if later is not None:
        later = make_valid(later)
        prepare(later)

    rows = []
    for p in parts:
        if p.geom_type != "Polygon":
            continue
        area_ha = ha(p)
        if area_ha < MIN_PIECE_HA:
            continue
        row = {
            "prior_survey_year": prior,
            "survey_year": current,
            "interval": f"{prior}-{current}",
            "piece_ha": area_ha,
            "polsby_popper": polsby_popper(p),
            "geometry": p,
        }
        for d in BUFFERS:
            row[f"core_survival_frac_{d}m"] = survival_frac(p, eroded[d], area_ha)
        row["reappear_frac"] = (
            survival_frac(p, later, area_ha) if later is not None else np.nan
        )
        rows.append(row)
    return rows


def build_pieces(years, footprints) -> gpd.GeoDataFrame:
    all_rows = []
    for i, (prior, current) in enumerate(zip(years, years[1:])):
        t0 = time.time()
        next_year = years[i + 2] if i + 2 < len(years) else None
        rows = pieces_for_interval(prior, current, next_year, footprints)
        all_rows.extend(rows)
        print(f"  {prior}->{current}: {len(rows)} pieces in {time.time()-t0:.1f}s")
    return gpd.GeoDataFrame(all_rows, crs=f"EPSG:{CRS_NZTM}")


def main():
    ensure_dirs()
    require(PARCELS_ENRICHED, TRANSITIONS)

    if RETIRED_PIECES_GEO.exists():
        print(f"Cache found -- loading {RETIRED_PIECES_GEO.name} "
              f"(delete it to force a full recompute).")
        pieces = gpd.read_parquet(RETIRED_PIECES_GEO)
    else:
        gdf = gpd.read_parquet(PARCELS_ENRICHED).to_crs(epsg=CRS_NZTM)
        gdf["geometry"] = gdf.geometry.apply(make_valid)
        years = sorted(gdf["survey_year"].unique().tolist())
        print(f"Loaded {len(gdf)} parcels, {len(years)} survey years, CRS={gdf.crs}")

        print("Dissolving annual footprints...")
        footprints = dissolve_footprints(gdf)

        print("Computing retired pieces per interval (one-off; cached from here on):")
        pieces = build_pieces(years, footprints)
        pieces.to_parquet(RETIRED_PIECES_GEO)
        print(f"{len(pieces)} retired pieces, {pieces['piece_ha'].sum():.1f} ha total "
              f"-> cached to {RETIRED_PIECES_GEO.name}")

    # --- conservation check against the existing transitions.csv -----------
    trans = pd.read_csv(TRANSITIONS)
    by_interval = pieces.groupby(["prior_survey_year", "survey_year"])["piece_ha"].sum()
    for _, r in trans.iterrows():
        key = (r["prior_survey_year"], r["survey_year"])
        got = by_interval.get(key, 0.0)
        assert abs(got - r["retired_ha"]) < AREA_TOL_HA, (
            f"{key}: pieces sum to {got:.4f} ha but transitions.csv says "
            f"{r['retired_ha']:.4f} ha -- exploded pieces lost or gained area"
        )
    print("\nPiece areas reconcile with transitions.csv retired_ha per interval.")

    # --- sensitivity of the banded split to the buffer distance ------------
    gross = pieces["piece_ha"].sum()
    print(f"\nBand totals across candidate buffer distances "
          f"(headline={HEADLINE_BUFFER}m, gross retired={gross:.1f} ha):")
    for d in BUFFERS:
        bands_d = pieces[f"core_survival_frac_{d}m"].apply(classify)
        tot = pieces.groupby(bands_d)["piece_ha"].sum()
        g = tot.get("likely_genuine_removal", 0.0)
        a = tot.get("ambiguous", 0.0)
        m = tot.get("likely_measurement_change", 0.0)
        print(f"  {d:>2}m  genuine={g:8.1f} ha ({g/gross*100:5.1f}%)   "
              f"ambiguous={a:8.1f} ha ({a/gross*100:5.1f}%)   "
              f"measurement={m:8.1f} ha ({m/gross*100:5.1f}%)")

    # --- reappearance sanity check (does NOT set the threshold) ------------
    pieces["band"] = pieces[f"core_survival_frac_{HEADLINE_BUFFER}m"].apply(classify)
    reap = pieces.dropna(subset=["reappear_frac"]).groupby("band").agg(
        n=("reappear_frac", "size"),
        mean_reappear_frac=("reappear_frac", "mean"),
        ha=("piece_ha", "sum"),
    )
    print("\nReappearance-in-next-survey by band (sanity check, not threshold input):")
    print(reap.round(3).to_string())

    pieces.to_parquet(RETIRED_PIECES_GEO)  # persist the "band" column too

    # --- per-interval banded classification (headline buffer) --------------
    per_interval = (
        pieces.groupby(["prior_survey_year", "survey_year", "band"])["piece_ha"]
        .sum().unstack(fill_value=0.0)
    )
    for col in BAND_ORDER:
        if col not in per_interval.columns:
            per_interval[col] = 0.0
    per_interval = per_interval[BAND_ORDER]
    per_interval["retired_ha_total"] = per_interval.sum(axis=1)
    per_interval["measurement_change_pct"] = (
        per_interval["likely_measurement_change"] / per_interval["retired_ha_total"] * 100
    )
    per_interval = per_interval.reset_index()
    per_interval.to_csv(RETIREMENT_CLASSIFICATION, index=False)

    print(f"\nPer-interval banded split (headline buffer={HEADLINE_BUFFER}m):")
    print(per_interval.round(1).to_string(index=False))

    totals = per_interval[BAND_ORDER + ["retired_ha_total"]].sum()
    print(f"\n2000-2025 TOTAL ({totals['retired_ha_total']:.1f} ha gross retired):")
    for col in BAND_ORDER:
        print(f"  {col:<28} {totals[col]:8.1f} ha "
              f"({totals[col]/totals['retired_ha_total']*100:5.1f}%)")
    print(f"\nWrote {RETIREMENT_CLASSIFICATION.name} and {RETIRED_PIECES_GEO.name}")


if __name__ == "__main__":
    main()
