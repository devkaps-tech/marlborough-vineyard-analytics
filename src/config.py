"""
Shared paths, constants and small helpers for the pipeline.

Every script imports from here rather than recomputing paths, so a new data
release only needs changing in one place. In particular the source geodatabase
is resolved by *glob*, not by its UUID folder name -- that UUID changes between
downloads from data.govt.nz.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
STATS_DIR = PROCESSED_DIR / "stats"
TABLEAU_DIR = DATA_DIR / "tableau"
REPORTS_DIR = BASE_DIR / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

ZIP_PATH = RAW_DIR / "vineyard_gdb.zip"
GDB_EXTRACT_DIR = RAW_DIR / "vineyard_gdb"
GDB_LAYER = "VineyardData"

# --- artifact chain -------------------------------------------------------
# Each step reads the previous file and writes a NEW one. Nothing is mutated
# in place, so re-running an early step can never silently strip a column
# that a later step added.
PARCELS_CLEAN = PROCESSED_DIR / "parcels_clean.parquet"
PARCELS_ENRICHED = PROCESSED_DIR / "parcels_enriched.parquet"
YEARLY_SUMMARY = PROCESSED_DIR / "yearly_summary.csv"
SUBREGION_SUMMARY = PROCESSED_DIR / "subregion_summary.csv"
FOOTPRINT_BY_YEAR = PROCESSED_DIR / "footprint_by_year.csv"
TRANSITIONS = PROCESSED_DIR / "transitions.csv"
TRANSITIONS_SUBREGION = PROCESSED_DIR / "transitions_by_subregion.csv"
NEW_PLANTINGS_GEO = PROCESSED_DIR / "new_plantings.parquet"
MODEL_FITS = STATS_DIR / "model_fits.json"

# --- coordinate reference systems ----------------------------------------
# 4326 (WGS84) for interoperability: Tableau, PostGIS, web maps.
# 2193 (NZTM2000) whenever we need true metres -- areas, distances, DBSCAN.
CRS_WGS84 = 4326
CRS_NZTM = 2193

ALL_DIRS = (PROCESSED_DIR, STATS_DIR, TABLEAU_DIR, REPORTS_DIR, FIGURES_DIR)


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def find_gdb() -> Path:
    """Locate the source File Geodatabase, unzipping it first if needed."""
    if not GDB_EXTRACT_DIR.exists():
        import zipfile

        if not ZIP_PATH.exists():
            raise FileNotFoundError(
                f"No geodatabase at {GDB_EXTRACT_DIR} and no zip at {ZIP_PATH}. "
                f"See data/README.md for where to download the source."
            )
        with zipfile.ZipFile(ZIP_PATH) as z:
            z.extractall(GDB_EXTRACT_DIR)

    gdb_dirs = sorted(GDB_EXTRACT_DIR.glob("*.gdb"))
    if not gdb_dirs:
        raise FileNotFoundError(f"No .gdb folder found under {GDB_EXTRACT_DIR}")
    if len(gdb_dirs) > 1:
        raise RuntimeError(
            f"Expected exactly one .gdb under {GDB_EXTRACT_DIR}, found "
            f"{len(gdb_dirs)}: {[p.name for p in gdb_dirs]}. Remove the stale one."
        )
    return gdb_dirs[0]


def require(*paths: Path) -> None:
    """Fail fast with an actionable message when an upstream artifact is missing."""
    missing = [p for p in paths if not p.exists()]
    if missing:
        names = "\n  ".join(str(p.relative_to(BASE_DIR)) for p in missing)
        raise FileNotFoundError(
            f"Missing required input(s):\n  {names}\n"
            f"Run `python3 src/run_pipeline.py` to rebuild the chain in order."
        )
