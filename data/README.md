# Data directory

Nothing in this directory is committed to git (see `.gitignore`). Every file here is either an
original download or reproducible by running the pipeline.

## Source

**Marlborough vineyard aerial survey**, Marlborough District Council, published on
[data.govt.nz](https://data.govt.nz). An aerial-photography-derived polygon layer of planted
vineyard area, captured across 18 survey years between 2000 and 2025.

The release is distributed in several equivalent formats (File Geodatabase, Shapefile, GeoPackage,
CSV, XLSX, KMZ). This project reads the **File Geodatabase**, which is the only format that
preserves both geometry and attributes in one file.

### Fetching it

Download the File Geodatabase export and place it at:

```
data/raw/vineyard_gdb.zip
```

`src/01_explore.py` unzips it to `data/raw/vineyard_gdb/<uuid>.gdb`. The UUID in the folder name
varies between downloads, so the pipeline resolves it by glob (`src/config.py`) rather than
hardcoding it.

The layer is named `VineyardData`.

## Source schema — and what it constrains

The raw attribute table carries only four fields:

| Field | Notes |
|---|---|
| `FID` | Row id within the export. **Not** a stable parcel identifier. |
| `Year` | Survey year. The only real temporal key. |
| `MidYearDate` | Always 1 July of `Year`. **Synthetic placeholder** — carries no information beyond `Year`. |
| `AreaHectares` | Parcel area as supplied. |

Consequences that shape every analysis in this project:

- **No variety, owner, producer, or yield data exists.** All statistics derive from geometry, area,
  and year alone.
- **No parcel identity persists across years.** Each survey is an independent polygon snapshot, so
  cross-year parcel tracking is impossible from the attributes. `parcel_id` in the processed
  outputs is a surrogate (`"{year}-{n}"`), unique within a year only.
- **Survey years are irregular** — gaps at 2013→2018 and 2018→2020. Interval figures must be
  annualised before comparison.
- **`AreaHectares` summed per year double-counts** any overlapping polygons within that year. The
  dissolved footprint from `src/05_overlay_transitions.py` is the honest area measure.

## Layout

| Path | Produced by | Contents |
|---|---|---|
| `raw/vineyard_gdb.zip` | manual download | original File Geodatabase export |
| `raw/vineyard_gdb/` | `01_explore.py` | unzipped `.gdb` |
| `processed/parcels_clean.parquet` | `02_transform.py` | repaired, reprojected (EPSG:4326) parcels |
| `processed/parcels_enriched.parquet` | `03_subregion_clustering.py` | above + derived `subregion` |
| `processed/yearly_summary.csv` | `02_transform.py` | naive per-year rollup |
| `processed/subregion_summary.csv` | `03_subregion_clustering.py` | per-year × sub-region rollup |
| `processed/footprint_by_year.csv` | `05_overlay_transitions.py` | dissolved footprint vs naive sum |
| `processed/transitions.csv` | `05_overlay_transitions.py` | new / retired / persisting hectares per interval |
| `processed/stats/` | `06_statistics.py` | model fits, growth rates, diagnostics |
| `tableau/` | `07_export_tableau.py` | dashboard-ready CSV + simplified GeoJSON |

Rebuild everything with:

```bash
python3 src/run_pipeline.py
```
