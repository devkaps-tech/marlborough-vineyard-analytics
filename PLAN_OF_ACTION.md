# Plan of Action — resume point

**Checkpoint taken:** 2026-09-11, after Phase 2 (spatial overlay) verified green.
**Full original plan:** `~/.claude/plans/draft-a-plan-of-fuzzy-reef.md`

Goal: Tableau Public dashboard + statistically defensible case study from the Marlborough
District Council vineyard aerial survey (2000–2025, 18 surveys, 59,099 parcel-years).

---

## Done and verified

**Phase 0 — repo hygiene**
- Deleted the dead `.venv_broken_ignore/` (pip/setuptools only, no real deps), the 61 MB orphaned
  `vineyard_parcels.geojson`, and the 4 orphaned PNGs. Data dir went 82 MB → 46 MB.
  PNGs were backed up to the session scratchpad (`orphaned-figures-backup/`) — they are
  disposable once `08_figures.py` exists.
- Rewrote `.gitignore`: ignores `data/*` but re-includes `data/README.md` (git cannot un-ignore a
  file inside an excluded directory — this needed `data/*`, not `data/`).
- Wrote `data/README.md` documenting the data.govt.nz source, the four-field source schema, and
  the constraints it imposes.

**Phase 1 — pipeline streamlining**
- Added `src/config.py`: all paths, CRS constants, the artifact chain, `find_gdb()` (resolves the
  `.gdb` by glob — the UUID changes per download), and `require()` for fail-fast messages.
- Broke the in-place mutation hazard. The chain is now immutable:
  `parcels_clean.parquet` (02) → `parcels_enriched.parquet` (03) → overlay outputs (05).
  Re-running 02 can no longer silently strip `subregion`.
- Hardened the DBSCAN labelling in `03`. Labels are no longer keyed to raw cluster ids; each large
  cluster is matched to a known anchor coordinate and the script **raises** if a cluster lands
  >0.15° from its anchor or if two anchors claim the same cluster. Verified: clusters landed
  0.003° and 0.004° from their anchors.
- Added `annualised_pct_growth` to `yearly_summary.csv` — survey gaps (2013→2018, 2018→2020) make
  raw interval growth non-comparable. 2018's headline +15.3% is only **+2.9%/yr**.
- Step 02 reproduces the original figures exactly (2025: 32,792.83 ha, 5,079 parcels).

**Phase 2 — `src/05_overlay_transitions.py`** ✅ all assertions pass
- Dissolves each year's parcels in EPSG:2193, then set-differences consecutive pairs into
  `new_ha` / `retired_ha` / `persisting_ha`.
- Area-conservation assertions (`persisting + new == footprint(t1)`,
  `persisting + retired == footprint(t0)`) hold for all 17 intervals to <0.01 ha.
- Outputs: `footprint_by_year.csv`, `transitions.csv`, `transitions_by_subregion.csv`,
  `new_plantings.parquet` (35,595 polygons).

---

## Findings so far — including one that killed a premise

**The intra-year double-counting premise was WRONG.** I expected the dissolved footprint to come
in well below the naive sum-of-parcels. Maximum overlap across all 18 years is **0.31 ha out of
32,792 ha (0.0009%)** — floating-point noise. The source polygons are topologically clean and
effectively non-overlapping within a year, so `footprint_ha ≈ naive_sum_ha` and
`net_change_ha == naive_net_change_ha` exactly.

This is a **validation of the source, not a correction to it**, and it must not be presented as a
headline finding. Report it as one line: the dissolve confirms the published areas are sound.

**The gross-vs-net premise was CONFIRMED, and is the real story.**

| Measure | 2000–2025 |
|---|---|
| Gross new planted | 37,630 ha |
| Gross retired | 7,999 ha |
| Net change | 29,631 ha |
| Churn ratio | **1.27×** |

The net series — the only thing the published data shows — hides ~8,000 ha of land leaving
production. Several intervals are dominated by retirement: 2010→11 retired **403%** of what it
gained, 2011→12 **199%**. That aligns with the post-GFC NZ wine glut, which is a real and
tellable story.

**Sliver robustness: checked, and the numbers hold.** 90% of the 35,595 new-planting pieces are
under 1 ha, but they carry only **4.1%** of the area; pieces under 0.01 ha are 53% of the count
and 0.11% of area. So boundary jitter from re-digitisation does not threaten the area-weighted
conclusions. It does mean the map layer should drop sub-hectare pieces — 90% fewer features for
4% area loss.

---

## Open question that must be settled before the report makes claims

**Is `retired_ha` genuine vine removal, or re-digitisation boundary shift?** The sliver check rules
out *small* noise, but a survey that re-drew block boundaries at finer resolution could move whole
hectares without a vine being pulled. The suspicious intervals (2006→08, 2011→12, 2022→23) are
exactly where mean parcel size drops, which is the digitisation-change signature.

Plan: for each retired polygon, measure whether it is a compact standalone block (likely genuine
removal) or a thin fringe adjacent to a surviving footprint (likely boundary shift) — e.g. by
comparing perimeter²/area against the retired area, or by testing what survives a ±20 m buffer
erosion of the prior footprint. Report gross churn with an explicit "of which, plausibly
measurement change" band rather than asserting 7,999 ha of vine removal.

**Minor reconciliation item:** per-subregion new-planting pieces total 36,842 ha vs 37,630 ha for
the national overlay. The ~788 ha gap is because a sub-region's series starts at its own first
appearance (Awatere has no 2000 footprint, so 2000→2002 Awatere gains aren't counted). Explainable,
but confirm before quoting either number.

---

## Remaining work

**Phase 3 — `src/06_statistics.py`** (not started)
- Logistic + Gompertz fits to the footprint series via `scipy.optimize.curve_fit`; carrying
  capacity **K with bootstrapped CIs** (n=18, so bootstrap residuals, don't trust the covariance
  matrix); compare by **AICc**, not AIC.
- Robustness: refit on post-2005 only and report whether K moves materially.
- The retired/boundary-shift classification described above.
- Parcel-size distribution by year, framed explicitly as a digitisation diagnostic.
- Awatere vs Wairau share-of-new-plantings divergence.
- Outputs → `data/processed/stats/` + `model_fits.json` so report and dashboard quote one source.
- **Needs `scipy` added to `requirements.txt`** (currently present only transitively via
  scikit-learn).

**Phase 4 — `src/07_export_tableau.py` + `src/08_figures.py`** (not started)
- Export `yearly_metrics.csv`, `subregion_metrics.csv`, simplified `parcels.geojson`
  (simplify in NZTM then reproject; target <10 MB), `new_plantings.geojson` (drop <1 ha pieces).
- `tableauhyperapi` is **not installed** and may not support Python 3.13 — CSV + GeoJSON is a
  sufficient Tableau Public path. Do not let Hyper block this phase.
- `08_figures.py` regenerates all report PNGs into `reports/figures/` (tracked in git).
- Dashboard: growth curve + fitted K · new-plantings map by interval · Awatere vs Wairau stacked
  area · annualised growth bars showing the contraction · KPI row. Caption that `subregion` is
  algorithmically derived.

**Phase 5 — case study** (not started)
- `REPORT.md`, then publish as an Artifact for a shareable link.
- Lead with gross-vs-net churn and the saturation estimate. Frame the methodological honesty —
  including the premise that didn't survive contact with the data — as the analyst judgment on
  display.
- Rewrite `README.md` (status checkboxes are still all unchecked) and update `CLAUDE.md` to match
  the new artifact chain and script numbering. **Both are currently stale** w.r.t. the renamed
  artifacts and the new `config.py` / `05_*` script.

**Deferred, not blocking**
- `04_load_to_supabase.py` still references the old `vineyard_parcels.parquet` filename and will
  break — it needs updating to `PARCELS_ENRICHED`. Not on the critical path since Tableau Public
  reads files, not Postgres, but it is currently broken and should not be presented as working.
- `sql/schema.sql` has no table for the new transition/footprint outputs.
- **Rotate the Supabase password** — it sat in plaintext in `.env` (correctly gitignored, and
  verified absent from the first commit, but rotate anyway).
- `notebooks/` is empty; either use it or drop it.
- ~10 redundant exports of the same release clutter the parent directory, outside the repo.

---

## How to resume

```bash
cd /Users/home/Documents/Marlborough-Vineyard/vineyard-analytics-project
python3 src/run_pipeline.py          # NOTE: not yet written -- see below
```

`src/run_pipeline.py` is **still to be written** (orchestrator + mtime staleness check). Until it
exists, run in order:

```bash
python3 src/01_explore.py            # optional, read-only
python3 src/02_transform.py          # -> parcels_clean.parquet, yearly_summary.csv
python3 src/03_subregion_clustering.py  # -> parcels_enriched.parquet, subregion_summary.csv
python3 src/05_overlay_transitions.py   # -> footprint/transitions/new_plantings
```

Verification that must keep passing: the area-conservation assertions in `05` (they are live
`assert`s, so the script fails loudly), dissolved footprint ≤ naive sum every year, and 2025
footprint ≈ 32,792 ha.
