"""
Step 1: Explore the raw vineyard geodatabase.

Read-only. Unzips the File Geodatabase if needed, lists its layers, and prints
schema / CRS / row count / geometry validity so we know exactly what we're
working with before any transform logic runs.

Usage:
    python3 src/01_explore.py
"""
import sys
from pathlib import Path

import geopandas as gpd
import pyogrio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import GDB_LAYER, find_gdb  # noqa: E402


def main():
    gdb_path = find_gdb()
    print(f"Geodatabase: {gdb_path}")

    layers = pyogrio.list_layers(gdb_path)
    print("\nLayers found:", layers)

    gdf = gpd.read_file(gdb_path, layer=GDB_LAYER)

    print(f"\nRow count: {len(gdf)}")
    print(f"CRS: {gdf.crs}")
    print(f"Columns: {list(gdf.columns)}")
    print(f"Geometry types:\n{gdf.geometry.geom_type.value_counts()}")
    print(f"Invalid geometries: {(~gdf.geometry.is_valid).sum()}")
    print(f"Year range: {gdf['year'].min():.0f} - {gdf['year'].max():.0f}")
    print(f"Survey years ({gdf['year'].nunique()}): {sorted(gdf['year'].unique())}")
    print(f"Bounds (native CRS): {gdf.total_bounds}")

    print(
        "\nData caveats confirmed from the source schema:\n"
        "  - No persistent parcel ID across years. Each survey year is an\n"
        "    independent polygon snapshot, not a tracked entity.\n"
        "  - 'coverageye' (MidYearDate) is always 1 July of 'year' -- a synthetic\n"
        "    placeholder carrying no information beyond the year itself.\n"
        "  - No variety / owner / producer / yield attributes exist. All analysis\n"
        "    must derive from geometry + area + year alone."
    )


if __name__ == "__main__":
    main()
