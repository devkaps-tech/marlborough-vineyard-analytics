# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Portfolio geospatial analytics project tracking 25 years (2000–2025) of vineyard expansion in
Marlborough, NZ, from the Marlborough District Council's aerial vineyard survey (data.govt.nz).
Flow: ArcGIS File Geodatabase → cleaned GeoParquet/CSV → Postgres/PostGIS on Supabase → Tableau.

Not a git repository. No test suite, linter, or build step — there is nothing to run beyond the
pipeline scripts themselves.

## Commands

Dependencies are installed into the **system** Python 3.13 (`/Library/Frameworks/Python.framework`),
not a virtualenv. `.venv_broken_ignore/` is a dead Python 3.10 venv — ignore it; don't activate it.

```bash
pip3 install -r requirements.txt      # if deps are missing

python3 src/01_explore.py             # inspect raw .gdb (read-only, safe to re-run)
python3 src/02_transform.py           # → vineyard_parcels.parquet, yearly_summary.csv
python3 src/03_subregion_clustering.py # → adds subregion, → subregion_summary.csv
python3 src/04_load_to_supabase.py    # applies schema, TRUNCATEs, full reload
```

The steps are strictly ordered and each writes into `data/processed/`.

## Pipeline coupling (read before editing any script)

**Step 03 rewrites step 02's output in place.** It reads `vineyard_parcels.parquet`, adds
`subregion`, and writes the same file back. So re-running 02 alone silently drops the `subregion`
column and leaves `subregion_summary.csv` stale — **02 and 03 must always be re-run as a pair**,
and 04 will fail or load incomplete rows if 03 was skipped.

**`CLUSTER_LABELS` in 03 is keyed by raw DBSCAN cluster IDs** (0 → Wairau/Southern Valleys,
1 → Awatere). Those IDs are an artifact of the current `eps=1500, min_samples=15`. Changing either
parameter, or the input parcel set, can reorder or renumber clusters and silently mislabel whole
regions. If you touch the DBSCAN call, re-verify which cluster is which by printing each cluster's
mean lon/lat before trusting the output.

**The MultiPolygon normalization in `02_transform.clean()` exists because of `sql/schema.sql`.**
The `geom` column is fixed-typed `geometry(MultiPolygon, 4326)`; `buffer(0)` repair can collapse a
MultiPolygon to a single Polygon, which psycopg2 then rejects on load. Don't remove that step
without also loosening the column type.

**Step 04 is a full reload, not an upsert** — it truncates all three tables inside a transaction
before inserting. Safe to re-run; not safe to run against a database holding data you care about.
It also requires the **Session** pooler connection string (port 5432, not the transaction pooler on
6543) because it executes DDL from `sql/schema.sql`.

`02_transform.py` hardcodes the source geodatabase UUID path
(`data/raw/vineyard_gdb/45dd4c20-....gdb`) and layer name `VineyardData`, while `01_explore.py`
globs for `*.gdb` instead. A new data release will break 02 until that constant is updated.

## Data semantics that affect every analysis

- **There is no persistent parcel ID across survey years.** Each year is an independent polygon
  snapshot. `parcel_id` is a surrogate (`"{year}-{n}"`), unique within a year only. Any
  cross-year parcel tracking, churn, or "same parcel changed" analysis is **not** supported by
  this data and would be wrong to compute from `parcel_id`.
- **Survey years are irregular** (gaps 2013→2018, 2018→2020). Raw `pct_growth` is not comparable
  between rows; that's why `yearly_summary` carries `years_since_prior_survey`. Annualize before
  comparing.
- **Average parcel size falls from ~27 ha to ~6.5 ha** across the series. This is mostly finer
  polygon digitisation in later surveys, not real fragmentation — don't present it as a trend.
- **`subregion` is algorithmically derived, not an official viticultural boundary.** DBSCAN
  recovers the Wither Hills gap separating Awatere from the Wairau plains, but cannot separate
  Wairau Valley proper from the Southern Valleys (they're contiguous), so those stay merged in one
  bucket. Replacing this with a real named-boundary join is a known open follow-up.
- Reprojection is to EPSG:4326 for Tableau/PostGIS/web-map interoperability; DBSCAN clusters in
  EPSG:2193 (NZTM2000) because it needs metric distances.

## Repo state

- README checkboxes are stale — steps 01–03 have been run and their outputs are committed to
  `data/processed/`. Steps 04 (Supabase load), Tableau dashboard, and the GitHub Actions +
  Hyper API refresh pipeline are not done.
- `vineyard_parcels.geojson` (61 MB) and the four `*.png` maps in `data/processed/` are **not**
  produced by anything in `src/` — they were made ad hoc and aren't reproducible from the pipeline.
- `.env` contains a real filled-in `DATABASE_URL`. It's gitignored, but the project isn't under
  version control yet; check `.gitignore` is honored before any first commit.
- `notebooks/` is empty.
