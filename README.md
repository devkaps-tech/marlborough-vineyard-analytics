# Marlborough Vineyard Growth Intelligence

Portfolio analytics project tracking 25 years of vineyard expansion in
Marlborough, NZ, from the Marlborough District Council's aerial vineyard
survey (published on data.govt.nz).

## Status
- [x] Raw data sourced (File Geodatabase, CSV, KMZ, XLSX exports of the same release)
- [ ] Explore & validate raw data
- [ ] Clean + transform (yearly summary, subregion tagging)
- [ ] Load into Postgres/PostGIS (Supabase)
- [ ] Tableau dashboard
- [ ] Automated refresh pipeline (GitHub Actions + Tableau Hyper API)

## Project layout
- `data/raw/` — original downloads (gitignored, kept locally only)
- `data/processed/` — cleaned outputs (GeoParquet, CSV summaries)
- `src/` — ETL scripts
- `sql/` — table DDL for the warehouse
- `notebooks/` — exploration

See the full project plan for architecture and rationale.
