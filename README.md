# Marlborough Vineyard Growth Intelligence

Twenty-five years of vineyard expansion in Marlborough, New Zealand, recovered from a dataset that
cannot track change directly.

**[Read the case study →](REPORT.md)**

The Marlborough District Council's aerial vineyard survey (via data.govt.nz) gives 18 survey years
between 2000 and 2025 and 59,099 parcel polygons — but **no persistent parcel identifier across
years**, and only four attributes in total. Each survey is an independent snapshot. This project
recovers real land-use change from that by dissolving each year's footprint and taking set
differences between consecutive surveys.

## Headline findings

| | |
|---|---|
| Planted footprint 2000 → 2025 | 3,162 → **32,793 ha** (10.4×) |
| Gross new planting | **37,630 ha** |
| Gross retired | **7,999 ha** — of which ~6,200 ha is likely genuine removal |
| Churn ratio | **1.27×** gross new to net change |
| Carrying capacity | **not identified** — see below |

The published net series hides roughly 8,000 ha of land leaving production. The saturation analysis
produced a **negative result**: with 18 irregularly-spaced observations of a series that has not
turned over, the 95% interval on carrying capacity runs past 240,000 ha. The report says so rather
than quoting a number.

![Planted vineyard footprint with saturation fits](reports/figures/growth_curve.png)

## Pipeline

```bash
pip3 install -r requirements.txt

python3 src/01_explore.py               # inspect the raw .gdb (read-only)
python3 src/02_transform.py             # clean, repair geometry, reproject
python3 src/03_subregion_clustering.py  # DBSCAN sub-regions, anchored to real coordinates
python3 src/05_overlay_transitions.py   # set-difference consecutive surveys  ← the core
python3 src/06_statistics.py            # saturation fits, bootstrap CIs, AICc
python3 src/07_export_tableau.py        # Tableau-ready CSV + GeoJSON
python3 src/08_figures.py               # report figures
python3 src/09_retirement_forensics.py  # removal vs re-digitisation
python3 src/10_build_dashboard.py       # dashboard.html
```

Steps are ordered; each writes a new artifact rather than mutating its input.
`src/04_load_to_supabase.py` loads the results into Postgres/PostGIS and is optional — Tableau
Public reads files, not a database.

Data is not committed. Everything under `data/` is reproducible from the raw download;
see [`data/README.md`](data/README.md) for the source.

## Dashboard

`dashboard.html` (generated, open it locally) is both a working replica of the Tableau dashboard
and its build spec: each worksheet shows the live chart beside the exact Tableau recipe — source
file, mark type, shelf placements, calculated-field formulas, and the places Tableau behaves
differently.

Tableau-ready exports land in `data/tableau/` with a data dictionary. Spatial layers are
purpose-built rather than one general geometry file, because a full all-years parcel layer is 36 MB
even simplified — new plantings ≥0.5 ha come to 2.8 MB and carry 97% of the area.

## Verification

```bash
python3 tests/test_invariants.py     # 20 checks on the pipeline's outputs
python3 tests/test_repo_hygiene.py   # 9 checks on the repo itself
```

Both run standalone; pytest is optional. The invariant suite asserts that the overlay's set algebra
closes in both directions, that sub-region series reconcile with the national total, that areas are
measured in a projected CRS, and that every headline figure quoted in the documents matches what the
pipeline computed. It has caught two real bugs so far — a sub-region first-appearance error losing
788 ha, and a rounding error in a project document.

Two review passes run as subagents (`.claude/agents/`): a statistical reviewer that caught the
saturation headline being backwards, and a spatial forensics agent that classified the retired land.

## Notes on the data

- **No parcel identity across years** — no survival analysis or per-parcel trajectories are possible.
- **Irregular survey intervals** (gaps 2013–2018, 2018–2020) — always annualise before comparing.
- **Mean parcel size falls 27 → 6.5 ha** — that is digitisation resolution, not fragmentation.
- **Sub-regions are algorithmically derived** and cannot separate Wairau proper from the Southern
  Valleys.
- **No causal variables exist** — no price, weather or planting intent. Correlation only.

## Stack

Python · GeoPandas / Shapely 2 · scikit-learn (DBSCAN) · SciPy · matplotlib · PostGIS · Tableau Public
