"""
Step 3: Derive an approximate sub-region tag per parcel.

The source data has no region attribute, and no authoritative boundary layer
for Marlborough's named wine sub-regions was available to join against. As a
documented, defensible substitute, this derives sub-regions from the geometry
itself via DBSCAN density clustering on parcel centroids (in NZTM2000 metres),
then maps clusters to real place names *by their actual coordinates*.

DBSCAN recovers the split Marlborough's geography implies: the Wither Hills
range physically separates the Wairau Plains / Southern Valleys corridor from
the Awatere Valley, so density clustering finds that gap with no manual
boundary. It does NOT reliably separate Wairau Valley proper from the Southern
Valleys -- those are contiguous with no physical gap -- so they stay merged in
one bucket. That finer split needs an authoritative named-boundary source and
is a documented follow-up, not something this script claims to solve.

IMPORTANT: `subregion` is an approximate, algorithmically-derived label, not an
official viticultural boundary. Treat it as "best available".

Labels are assigned by matching each large cluster's mean coordinate to a known
anchor, NOT by raw DBSCAN cluster id. Cluster ids are an artifact of the eps /
min_samples values and can renumber freely when those change; binding labels to
ids risks silently mislabelling entire regions. The assertions below fail loudly
instead.

Reads:  parcels_clean.parquet
Writes: parcels_enriched.parquet, subregion_summary.csv

Usage:
    python3 src/03_subregion_clustering.py
"""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    CRS_NZTM,
    CRS_WGS84,
    PARCELS_CLEAN,
    PARCELS_ENRICHED,
    SUBREGION_SUMMARY,
    ensure_dirs,
    require,
)

# DBSCAN parameters. 1500 m reach with 15 neighbours is wide enough to bridge
# the gaps between blocks along a valley floor, but narrower than the ~10 km
# of hill country separating the Wairau from the Awatere.
DBSCAN_EPS_M = 1500
DBSCAN_MIN_SAMPLES = 15

# Known anchor coordinates (lon, lat) for the two geographically unambiguous
# growing areas. Labels are assigned by proximity to these, so changing the
# DBSCAN parameters above cannot silently swap the region names.
ANCHORS = {
    "Wairau Valley / Southern Valleys": (173.85, -41.52),  # Renwick-Blenheim corridor
    "Awatere Valley": (174.05, -41.67),                     # south of the Wither Hills
}
# A cluster centroid further than this from its nearest anchor is not something
# we're willing to name (~15 km at this latitude).
ANCHOR_TOLERANCE_DEG = 0.15
# Clusters holding less than this share of all parcels get bucketed rather than
# given a precise valley name we cannot verify.
MINOR_CLUSTER_SHARE = 0.01
DEFAULT_LABEL = "Other / minor pocket (unverified)"


def assign_labels(labels: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> pd.Series:
    """Map DBSCAN cluster ids to place names via each cluster's real location."""
    frame = pd.DataFrame({"cluster": labels, "lon": lon, "lat": lat})

    # cluster -1 is DBSCAN's noise label; never name it
    sizes = frame[frame["cluster"] != -1]["cluster"].value_counts()
    min_size = len(frame) * MINOR_CLUSTER_SHARE
    candidates = sizes[sizes >= min_size].index.tolist()

    if len(candidates) < len(ANCHORS):
        raise RuntimeError(
            f"Expected at least {len(ANCHORS)} clusters holding >= "
            f"{MINOR_CLUSTER_SHARE:.0%} of parcels, found {len(candidates)}. "
            f"DBSCAN(eps={DBSCAN_EPS_M}, min_samples={DBSCAN_MIN_SAMPLES}) may "
            f"need retuning for this data."
        )

    means = frame[frame["cluster"].isin(candidates)].groupby("cluster")[["lon", "lat"]].mean()

    cluster_to_label: dict[int, str] = {}
    for name, (alon, alat) in ANCHORS.items():
        dist = np.hypot(means["lon"] - alon, means["lat"] - alat)
        best = dist.idxmin()
        if dist[best] > ANCHOR_TOLERANCE_DEG:
            raise RuntimeError(
                f"No cluster centroid lies within {ANCHOR_TOLERANCE_DEG} deg of "
                f"the '{name}' anchor ({alon}, {alat}); nearest is cluster {best} "
                f"at {dist[best]:.3f} deg. Refusing to guess -- verify the data "
                f"and DBSCAN parameters."
            )
        if best in cluster_to_label:
            raise RuntimeError(
                f"Cluster {best} is nearest to both '{cluster_to_label[best]}' and "
                f"'{name}'. The clusters are not separating as expected -- "
                f"inspect cluster centroids before trusting any label."
            )
        cluster_to_label[best] = name
        print(f"  cluster {best}: {name}  "
              f"(centroid {means.loc[best, 'lon']:.3f}E {means.loc[best, 'lat']:.3f}S, "
              f"{sizes[best]} parcels, {dist[best]:.3f} deg from anchor)")

    return frame["cluster"].map(cluster_to_label).fillna(DEFAULT_LABEL)


def main():
    ensure_dirs()
    require(PARCELS_CLEAN)

    gdf = gpd.read_parquet(PARCELS_CLEAN)

    # Cluster on centroids in a projected (metric) CRS -- NZTM2000. Centroids
    # are taken in NZTM and only then reprojected to lon/lat for the anchor
    # comparison; taking them directly on EPSG:4326 would compute a centroid in
    # degree space, which is both inaccurate and warns.
    proj_centroids = gdf.to_crs(epsg=CRS_NZTM).geometry.centroid
    X = np.column_stack([proj_centroids.x, proj_centroids.y])

    db = DBSCAN(eps=DBSCAN_EPS_M, min_samples=DBSCAN_MIN_SAMPLES).fit(X)
    print(f"DBSCAN found {len(set(db.labels_)) - (1 if -1 in db.labels_ else 0)} clusters "
          f"({(db.labels_ == -1).sum()} noise points)")

    wgs_centroids = proj_centroids.to_crs(epsg=CRS_WGS84)
    gdf["subregion"] = assign_labels(
        db.labels_, wgs_centroids.x.to_numpy(), wgs_centroids.y.to_numpy()
    ).to_numpy()

    print(f"\n{gdf['subregion'].value_counts().to_string()}")

    gdf.to_parquet(PARCELS_ENRICHED)

    subregion_summary = (
        gdf.groupby(["survey_year", "subregion"])
        .agg(total_parcels=("parcel_id", "count"),
             total_area_ha=("area_hectares", "sum"))
        .reset_index()
        .sort_values(["survey_year", "subregion"])
    )
    subregion_summary.to_csv(SUBREGION_SUMMARY, index=False)
    print(f"\nWrote {PARCELS_ENRICHED.name} and {SUBREGION_SUMMARY.name}")


if __name__ == "__main__":
    main()
