# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Portfolio geospatial analytics project tracking 25 years (2000–2025, 18 irregular surveys,
59,099 parcel-years) of vineyard expansion in Marlborough, NZ, from the Marlborough District
Council's aerial vineyard survey (data.govt.nz).

Target output: a **Tableau Public** dashboard plus a statistically defensible case study.
See `PLAN_OF_ACTION.md` for current status, findings, and what's left.

## Commands

Dependencies are in the **system** Python 3.13 (`/Library/Frameworks/Python.framework`), not a
virtualenv. `matplotlib` is declared but **not yet installed** (needed for step 08).

```bash
python3 src/01_explore.py               # inspect raw .gdb (read-only)
python3 src/02_transform.py             # → parcels_clean.parquet, yearly_summary.csv
python3 src/03_subregion_clustering.py  # → parcels_enriched.parquet, subregion_summary.csv
python3 src/05_overlay_transitions.py   # → footprint_by_year, transitions, new_plantings
python3 tests/test_invariants.py        # 19 invariant checks; pytest optional
```

Strictly ordered. `src/run_pipeline.py` (orchestrator + mtime staleness check) is **planned but
not yet written** — run the steps individually for now.

`src/04_load_to_supabase.py` is **currently broken**: it still references the pre-rename
`vineyard_parcels.parquet`. Not on the critical path (Tableau Public reads files, not Postgres),
but don't present it as working.

## Architecture

`src/config.py` centralises all paths, CRS constants, and the artifact chain. Import from there
rather than recomputing paths. It resolves the source `.gdb` by **glob**, because the UUID in its
folder name changes between downloads.

**The artifact chain is immutable** — each step reads the previous file and writes a new one:

```
raw .gdb → 02 → parcels_clean.parquet → 03 → parcels_enriched.parquet → 05 → transitions/footprint
```

This is deliberate. An earlier version had step 03 overwrite step 02's parquet in place, so
re-running 02 alone silently dropped the `subregion` column with no error. Don't reintroduce
in-place mutation.

**Step 05 is the analytical core.** It dissolves each survey year's parcels in EPSG:2193 and
set-differences consecutive years into `new_ha` / `retired_ha` / `persisting_ha`. This exists
because the source has no parcel identity across years — set operations on geometry are the only
way to recover real change. It carries live area-conservation assertions; if they fire, the
dissolve or the CRS is wrong, not the tolerance.

Two implementation details that are load-bearing:

- **Sub-regions are differenced across the full year list**, with an empty footprint standing in
  for years a sub-region has no parcels. Restricting to years it appears in drops its first
  appearance entirely — that bug lost 684 ha (Awatere 2002) and 104 ha (minor pockets 2005).
- **The MultiPolygon normalization in `02_transform.clean()` exists for `sql/schema.sql`**, whose
  `geom` column is fixed-typed `geometry(MultiPolygon, 4326)`. `buffer(0)` can collapse a
  MultiPolygon to a Polygon, which psycopg2 then rejects. Don't remove it without loosening the
  column type.

**Sub-region labels are assigned by geography, not cluster id.** `03` matches each large DBSCAN
cluster to a known anchor coordinate and **raises** if a cluster lands >0.15° away or if two
anchors claim the same cluster. An earlier version keyed labels to raw cluster ids `{0, 1}`, which
are an artifact of `eps`/`min_samples` and could renumber silently. Keep the assertions.

## Data semantics that affect every analysis

- **No persistent parcel ID across years.** Each survey is an independent snapshot. `parcel_id` is
  a surrogate (`"{year}-{n}"`), unique within a year only. Per-parcel tracking, survival analysis,
  or "this vineyard grew" claims are impossible — and wrong to compute from `parcel_id`.
- **Only four source attributes exist**: `FID, Year, MidYearDate, AreaHectares`. No variety, owner,
  producer, or yield. Everything derives from geometry + area + year.
- **`survey_date` is synthetic** — always 1 July. Never use it as a time axis; `survey_year` is the
  only real temporal key.
- **Survey years are irregular** (2013→2018, 2018→2020). Raw `pct_growth` is not comparable between
  rows — use `annualised_pct_growth`. 2018's headline +15.3% is only +2.9%/yr.
- **n = 18** for any time-series fit. Parcels are not independent observations of the growth process.
- **Mean parcel size falls 27 → 6.5 ha.** That's survey resolution, not fragmentation. Don't
  present it as a trend.
- **The naive sum-of-parcels is sound.** The dissolve was expected to find intra-year
  double-counting and found none (max overlap 0.0009%). Report that as validation of the source,
  not as a correction that discovered something.
- **`subregion` is algorithmically derived**, and cannot separate Wairau Valley proper from the
  Southern Valleys (contiguous, no physical gap). Any per-region claim inherits that.
- EPSG:4326 for storage/interoperability; **EPSG:2193 (NZTM2000) for every area and distance**.

## Two results that constrain what may be claimed

**Retired land is now classified, as a band and not a number.** Of 7,999 ha gross retired, ~6,201 ha
(77.5%) is likely genuine removal, 877 ha (11.0%) likely re-digitisation, 920 ha ambiguous — at a
10 m erosion threshold. That threshold does a lot of work: 86% genuine at 5 m, 38% at 40 m, and it
never stabilises. Always quote the band and the threshold. The 2011–12 contraction survives as
real (862 ha genuine after removing 382 ha of noise).

**Carrying capacity is NOT identified.** Post-2005 fits give K = 42,470 (logistic) / 45,563
(Gompertz) ha, but the 95% intervals reach 242,821 / 259,675 ha. Never quote a capacity figure or a
"years to saturation" projection. An earlier version of this analysis concluded the saturation model
was *misspecified* — that was wrong, an artefact of including the digitisation-distorted 2000/2002/
2005 surveys, and it was caught in review. Do not reintroduce it.

Also barred by the data: dating the growth resumption to 2018 (it resumed inside the unobserved
2013–2018 gap), counting discrete regimes (asserted, never statistically detected), and any causal
claim about the 2011–12 contraction (no price or weather variable exists).

## Verification

```bash
python3 tests/test_invariants.py     # pipeline outputs; standalone, pytest optional
python3 tests/test_repo_hygiene.py   # the repo itself, across full history
```

20 deterministic checks in the invariant suite. **Run after touching any transform or overlay logic** — the
area-conservation and reconciliation checks are the only thing between a CRS/set-algebra mistake
and a wrong published number. Checks whose inputs don't exist yet (Tableau exports,
`model_fits.json`) skip rather than fail. It found two real bugs on its first run.

Two tolerances, deliberately different: `AREA_TOL_HA = 0.01` for single set-operation identities,
`AGGREGATE_TOL_HA = 0.1` for comparisons summing several independently-computed series. Don't
collapse them — a structural bug shows up in whole hectares, so loosening the tight one hides real
breakage.

`test_documents_do_not_claim_retired_land_is_vine_removal` is an editorial guard, not a data check:
it blocks any doc from calling retired area "vines removed" until
`data/processed/stats/retirement_classification.csv` exists.

## Subagents

- **`digitisation-forensics`** (sonnet) — settles the retired-land question above. Writes
  `src/09_retirement_forensics.py` and returns a banded split with threshold sensitivity.
- **`stats-reviewer`** (opus) — read-only adversarial audit of statistical claims (AICc not AIC,
  bootstrapped CIs, no causal language, no over-claiming K). Deliberately has no write tools, so
  it reports rather than quietly "fixing" numbers.

Both enforce token discipline: never load `parcels_enriched.parquet` (15 MB),
`new_plantings.parquet`, or the 59,099-row attributes CSV into context, and never re-run steps
02/03/05 (step 05 dissolves 59,099 polygons). Read the small CSVs in `data/processed/` instead.

## Repo state

- `README.md` status checkboxes are **stale** — steps 01–03 and 05 have all run.
- Data is gitignored; everything under `data/` is reproducible. `data/README.md` documents the
  source and is the one tracked file there.
- `.env` holds a live Supabase `DATABASE_URL`, correctly gitignored and verified absent from
  history. It sat in plaintext, so **rotate that password**.
- `notebooks/` is empty. `sql/schema.sql` has no tables for the step-05 outputs.

## Repo hygiene (public repository)

```bash
python3 tests/test_repo_hygiene.py   # 9 checks, runs against FULL commit history
```

The repo is public at `github.com/devkaps-tech/marlborough-vineyard-analytics`. Checks run over
`git rev-list --all`, not just the working tree — a secret removed in a later commit is still
published in the earlier one.

Optional pre-push guard (blocks on hygiene failure, warns on invariant failure):

```bash
ln -s ../../tests/pre-push-hook.sh .git/hooks/pre-push   # bypass once: git push --no-verify
```

`.gitignore` uses `data/*` plus `!data/README.md`, and that ordering is load-bearing — reverting
it to `data/` silently re-excludes the README, because git cannot un-ignore a file inside an
excluded directory. `test_data_readme_survives_the_data_exclusion` guards it.

Shared test plumbing lives in `tests/_harness.py` (skip/need/runner), so both suites run
standalone without pytest and still collect under it.

**`repo-auditor`** (sonnet, read-only) handles the judgement half: semantic leaks a regex can't
match, docs contradicting code, broken-but-shipped code, commit-message quality. It is barred from
every state-changing git command and has no write tools — it describes fixes for the main session
to apply, because history rewriting on a published repo is irreversible.
