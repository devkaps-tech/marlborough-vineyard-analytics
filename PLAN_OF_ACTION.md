# Plan of Action — COMPLETE

> All phases delivered. Kept as the project's working log: it records the two
> premises that failed and why, which is the part worth preserving.

**Last updated:** 2026-09-11, after the dashboard and case study landed.
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
32,793 ha (0.0009%)** — floating-point noise. The source polygons are topologically clean and
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
gained, 2011→12 **199%**. This *coincides with* the post-GFC NZ wine glut — but the dataset holds
no price, weather or planting-intent variable, so the report states the coincidence and stops
short of causation.

**Sliver robustness: checked, and the numbers hold.** 90% of the 35,595 new-planting pieces are
under 1 ha, but they carry only **4.1%** of the area; pieces under 0.01 ha are 53% of the count
and 0.11% of area. So boundary jitter from re-digitisation does not threaten the area-weighted
conclusions. It does mean the map layer should drop sub-hectare pieces — 90% fewer features for
4% area loss.

---

## ~~Open question~~ — SETTLED, as a band rather than a number

**Is `retired_ha` genuine vine removal, or re-digitisation boundary shift?** Answered by
`src/09_retirement_forensics.py`, which erodes the prior footprint and measures what fraction of
each retired piece survives — a thin rim against a surviving edge reads as boundary shift, a
compact interior block as real removal.

At a 10 m threshold: **6,201 ha (77.5%) likely genuine removal, 877 ha (11.0%) likely measurement
change, 920 ha (11.5%) ambiguous.** A shape signal agrees independently (measurement-change median
piece 0.008 ha, compactness 0.105; genuine 0.373 ha, 0.308).

**The threshold sensitivity is the real finding.** 86% genuine at 5 m, 38% at 40 m, and it never
stabilises — so this is a modelling choice, not a measurement, and every quote of it must carry the
band and the threshold. The reappearance cross-check was inconclusive (replanting takes longer than
one survey gap).

What survives: the 2011–12 contraction is real — 862 ha genuine after removing 382 ha of noise,
against 170 ha and 4.5 ha either side. 2008→09 and 2010→11 are NOT explained by measurement noise
(<1% each); their high retired/new ratios come from suppressed new planting, not inflated retirement.

**~~Minor reconciliation item~~ — RESOLVED, and it was a bug, not a rounding gap.** The
per-subregion series came to 36,842 ha against 37,630 ha nationally. The invariant suite localised
the 788 ha: sub-regions were differenced only across the years they appear in, so each one's
**first appearance was never counted as new land** — 683.79 ha for Awatere in 2002 and 104.35 ha
for the minor pockets in 2005, matching the shortfall exactly. A dashboard filtered to Awatere
would have shown zero new hectares in 2002 despite 684 ha appearing.

Fixed in `src/05_overlay_transitions.py`: every sub-region is now differenced across the full year
list with an empty footprint standing in for absent years, so a first appearance is attributed
wholly to new land. The two series now reconcile to 0.026 ha (0.00007%) — genuine floating-point
accumulation across three independent set-operation chains.

---

## Verification and review infrastructure (added)

**`tests/test_invariants.py`** — 20 deterministic checks, plus 9 in
`tests/test_repo_hygiene.py`. Both run standalone (no pytest needed) or under pytest if installed.
All green; the one skip is the editorial vine-removal guard, which correctly stands down now that
the forensics classification exists.

Covers: overlay area conservation both directions · footprint ≤ naive sum · sub-region/national
reconciliation · interval chaining and annualisation arithmetic · CRS correctness (parcels in
4326, areas in 2193) · geometry validity and `parcel_id` uniqueness · regression baselines
(18 survey years, 59,099 parcels, 2025 footprint) · Tableau export size and simplification
fidelity · and a document fact-check that every headline figure quoted in the markdown matches
what the pipeline computed.

It found two real problems on first run: the 788 ha first-appearance bug above, and a rounding
error in this very file (32,792 where 32,792.56 rounds to 32,793).

One guard is deliberately editorial: `test_documents_do_not_claim_retired_land_is_vine_removal`
fails the build if any doc calls retired area "vines removed/pulled/grubbed" before
`digitisation-forensics` has established the split. It auto-disables once
`stats/retirement_classification.csv` exists.

**`.claude/agents/digitisation-forensics.md`** (sonnet) — owns the retired-land question. Writes
`src/09_retirement_forensics.py`, caches classified pieces, returns a banded split with threshold
sensitivity. Tooled for read/write/bash; barred from touching the report, exports, or steps 01–05.

**`.claude/agents/stats-reviewer.md`** (opus) — adversarial audit of every statistical claim before
publication. Read-only by design: no write tools, so it reports rather than quietly "fixing"
numbers. Carries the n=18 / irregular-interval / synthetic-date constraints and a seven-point
audit checklist.

Both agent specs carry explicit token discipline (never load the 15 MB parquet or the 59k-row CSV;
never re-run steps 02/03/05; print aggregates only) because the whole point of delegating is to
keep heavy intermediate output out of the main context.

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
cd path/to/marlborough-vineyard-analytics
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
footprint ≈ 32,793 ha.
