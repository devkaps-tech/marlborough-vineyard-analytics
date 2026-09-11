"""
Deterministic invariant checks for the vineyard pipeline.

These were originally scoped as review subagents, but every check here is a
deterministic assertion about numbers -- so it belongs in code that runs the
same way every time, not in a model that might overlook something. The genuinely
judgement-shaped reviews live in .claude/agents/ instead.

Three groups:
  1. Spatial/arithmetic invariants -- the overlay's set algebra must close, areas
     must be non-negative, CRSs must be the ones we think they are.
  2. Tableau export audit -- sizes, and geometry simplification must preserve area.
  3. Document fact-check -- headline figures quoted in the markdown must match
     what the pipeline actually computed.

Groups 2 and 3 skip cleanly when their inputs don't exist yet, so the suite is
useful from the current checkpoint onward rather than only once everything is built.

Usage:
    python3 tests/test_invariants.py     # standalone, no dependencies beyond the pipeline's
    pytest tests/test_invariants.py -v   # if pytest is installed
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import need, run_tests, skip  # noqa: E402

import config  # noqa: E402  (_harness puts src/ on the path)


# Areas come from floating-point set operations on tens of thousands of
# polygons; they will not close exactly. 0.01 ha == 100 m^2.
AREA_TOL_HA = 0.01
# Comparisons that SUM several independently-computed series need more room,
# because each one carries its own set-operation error. Summing the three
# sub-region series against the national one lands ~0.03 ha out on 37,630 ha
# (0.00007%). Anything approaching this bound is still noise; a structural bug
# shows up in whole hectares, as the first-appearance bug did at 684 ha.
AGGREGATE_TOL_HA = 0.1

# Regression baselines. These are facts about the published 2000-2025 release,
# so a change here means either the source data changed or the pipeline broke --
# both warrant a human looking, which is the point of pinning them.
EXPECTED_SURVEY_YEARS = [2000, 2002, 2005, 2006, 2007, 2008, 2009, 2010, 2011,
                         2012, 2013, 2018, 2020, 2021, 2022, 2023, 2024, 2025]
EXPECTED_PARCEL_COUNT = 59_099
EXPECTED_2025_FOOTPRINT_HA = 32_792.56
EXPECTED_SUBREGIONS = {
    "Wairau Valley / Southern Valleys",
    "Awatere Valley",
    "Other / minor pocket (unverified)",
}


# =========================================================================
# 1. Spatial and arithmetic invariants
# =========================================================================

def test_survey_years_are_the_expected_set():
    need(config.FOOTPRINT_BY_YEAR)
    fp = pd.read_csv(config.FOOTPRINT_BY_YEAR)
    assert fp["survey_year"].tolist() == EXPECTED_SURVEY_YEARS, (
        f"survey years changed: got {fp['survey_year'].tolist()}"
    )


def test_dissolved_footprint_never_exceeds_naive_sum():
    """A union of polygons cannot cover more ground than the sum of their areas."""
    need(config.FOOTPRINT_BY_YEAR)
    fp = pd.read_csv(config.FOOTPRINT_BY_YEAR)
    bad = fp[fp["footprint_ha"] > fp["naive_sum_ha"] + AREA_TOL_HA]
    assert bad.empty, (
        f"footprint > naive sum for year(s) {bad['survey_year'].tolist()} -- "
        f"dissolve or CRS is wrong"
    )


def test_overlap_is_reported_consistently():
    need(config.FOOTPRINT_BY_YEAR)
    fp = pd.read_csv(config.FOOTPRINT_BY_YEAR)
    recomputed = fp["naive_sum_ha"] - fp["footprint_ha"]
    assert (recomputed - fp["overlap_ha"]).abs().max() < 1e-6, "overlap_ha inconsistent"


def test_overlay_area_conservation():
    """
    persisting + new must reassemble the later footprint, and
    persisting + retired the earlier one. If these fail the set algebra is
    broken -- this is the single most important check in the suite.
    """
    need(config.TRANSITIONS)
    t = pd.read_csv(config.TRANSITIONS)
    fwd = (t["persisting_ha"] + t["new_ha"] - t["footprint_ha"]).abs()
    back = (t["persisting_ha"] + t["retired_ha"] - t["footprint_prior_ha"]).abs()
    assert fwd.max() < AREA_TOL_HA, (
        f"persisting+new != footprint(t1); worst interval:\n"
        f"{t.loc[fwd.idxmax(), ['prior_survey_year', 'survey_year']].to_dict()} "
        f"off by {fwd.max():.4f} ha"
    )
    assert back.max() < AREA_TOL_HA, (
        f"persisting+retired != footprint(t0); worst interval:\n"
        f"{t.loc[back.idxmax(), ['prior_survey_year', 'survey_year']].to_dict()} "
        f"off by {back.max():.4f} ha"
    )


def test_net_change_is_new_minus_retired():
    need(config.TRANSITIONS)
    t = pd.read_csv(config.TRANSITIONS)
    diff = (t["net_change_ha"] - (t["new_ha"] - t["retired_ha"])).abs()
    assert diff.max() < 1e-6, "net_change_ha != new_ha - retired_ha"


def test_annualised_rates_divide_by_the_interval():
    need(config.TRANSITIONS)
    t = pd.read_csv(config.TRANSITIONS)
    assert (t["years_in_interval"] == t["survey_year"] - t["prior_survey_year"]).all()
    assert (t["years_in_interval"] > 0).all(), "non-positive survey interval"
    diff = (t["new_ha_per_year"] - t["new_ha"] / t["years_in_interval"]).abs()
    assert diff.max() < 1e-6, "new_ha_per_year is not new_ha / years_in_interval"


def test_intervals_chain_without_gaps():
    """Each interval must start where the previous ended -- no skipped surveys."""
    need(config.TRANSITIONS)
    t = pd.read_csv(config.TRANSITIONS).sort_values("survey_year")
    assert t["prior_survey_year"].tolist() == EXPECTED_SURVEY_YEARS[:-1]
    assert t["survey_year"].tolist() == EXPECTED_SURVEY_YEARS[1:]


def test_no_negative_areas():
    need(config.TRANSITIONS, config.FOOTPRINT_BY_YEAR)
    t = pd.read_csv(config.TRANSITIONS)
    fp = pd.read_csv(config.FOOTPRINT_BY_YEAR)
    for col in ["new_ha", "retired_ha", "persisting_ha", "footprint_ha"]:
        assert (t[col] >= -AREA_TOL_HA).all(), f"negative {col} in transitions"
    assert (fp["footprint_ha"] > 0).all(), "non-positive footprint"


def test_subregion_transitions_reconcile_with_the_total():
    """
    Sub-regions are spatially disjoint and cover every parcel, so their new area
    must sum to the national figure -- not merely stay under it.

    Checking both directions matters. An earlier version only checked the upper
    bound and so missed the opposite failure: sub-regions were differenced only
    across the years they appear in, which dropped each one's first appearance
    entirely (684 ha for Awatere in 2002, 104 ha for the minor pockets in 2005)
    and left the per-region series 788 ha short of the national one.
    """
    need(config.TRANSITIONS, config.TRANSITIONS_SUBREGION)
    total = pd.read_csv(config.TRANSITIONS).set_index("survey_year")["new_ha"]
    sub = pd.read_csv(config.TRANSITIONS_SUBREGION).groupby("survey_year")["new_ha"].sum()

    missing_years = set(total.index) - set(sub.index)
    assert not missing_years, f"no sub-region transitions for year(s) {sorted(missing_years)}"

    delta = (sub[total.index] - total).abs()
    assert delta.max() < AGGREGATE_TOL_HA, (
        f"sub-region new_ha does not reconcile with the national total; worst "
        f"year {delta.idxmax()} off by {delta.max():.2f} ha"
    )


def test_2025_footprint_matches_baseline():
    need(config.FOOTPRINT_BY_YEAR)
    fp = pd.read_csv(config.FOOTPRINT_BY_YEAR).set_index("survey_year")
    got = fp.loc[2025, "footprint_ha"]
    assert abs(got - EXPECTED_2025_FOOTPRINT_HA) < 1.0, (
        f"2025 footprint {got:.2f} ha drifted from baseline "
        f"{EXPECTED_2025_FOOTPRINT_HA} ha"
    )


def test_parcels_crs_and_geometry_are_valid():
    """
    Guards the CRS mistake that would silently corrupt every area: parcels are
    stored in 4326 for interoperability, but all areas must be measured in 2193.
    """
    need(config.PARCELS_ENRICHED)
    import geopandas as gpd

    gdf = gpd.read_parquet(config.PARCELS_ENRICHED)
    assert gdf.crs.to_epsg() == config.CRS_WGS84, f"expected 4326, got {gdf.crs}"
    assert len(gdf) == EXPECTED_PARCEL_COUNT, f"parcel count changed: {len(gdf)}"
    assert gdf.geometry.is_valid.all(), (
        f"{(~gdf.geometry.is_valid).sum()} invalid geometries survived step 02"
    )
    assert not gdf.geometry.is_empty.any(), "empty geometries present"
    assert gdf["parcel_id"].is_unique, "parcel_id is not unique"
    assert not gdf["subregion"].isna().any(), "null subregion"
    assert set(gdf["subregion"].unique()) <= EXPECTED_SUBREGIONS, (
        f"unexpected subregion label(s): {set(gdf['subregion'].unique()) - EXPECTED_SUBREGIONS}"
    )


def test_new_plantings_stored_in_metric_crs():
    """Areas were computed from this file, so it must be projected, not geographic."""
    need(config.NEW_PLANTINGS_GEO)
    import geopandas as gpd

    gdf = gpd.read_parquet(config.NEW_PLANTINGS_GEO)
    assert gdf.crs.to_epsg() == config.CRS_NZTM, (
        f"new_plantings must be EPSG:2193 for true areas, got {gdf.crs}"
    )
    assert (gdf["piece_ha"] > 0).all(), "non-positive piece area"


def test_yearly_summary_annualised_growth_is_present_and_sane():
    need(config.YEARLY_SUMMARY)
    y = pd.read_csv(config.YEARLY_SUMMARY)
    assert "annualised_pct_growth" in y.columns, (
        "annualised_pct_growth missing -- irregular survey intervals make raw "
        "pct_growth non-comparable between rows"
    )
    multi = y[y["years_since_prior_survey"] > 1].dropna(subset=["annualised_pct_growth"])
    assert not multi.empty
    # over a multi-year gap the annualised rate must sit below the raw one
    assert (multi["annualised_pct_growth"] < multi["pct_growth"]).all()


# =========================================================================
# 2. Tableau export audit
# =========================================================================

MAX_GEOJSON_MB = 10.0
MAX_CSV_MB = 50.0
# How much area a simplified geometry may lose before the map misleads.
MAX_SIMPLIFY_AREA_LOSS_PCT = 1.0


def test_tableau_exports_are_within_size_budget():
    need(config.TABLEAU_DIR)
    files = list(config.TABLEAU_DIR.glob("*"))
    if not files:
        skip("no Tableau exports yet")
    oversized = []
    for f in files:
        mb = f.stat().st_size / 1024**2
        cap = MAX_GEOJSON_MB if f.suffix.lower() in {".geojson", ".json"} else MAX_CSV_MB
        if mb > cap:
            oversized.append(f"{f.name} is {mb:.1f} MB (cap {cap} MB)")
    assert not oversized, "Tableau exports too large:\n  " + "\n  ".join(oversized)


def test_simplified_geometry_preserves_area():
    """
    Simplification is for file size; it must not move the numbers.

    Checks the snapshot layers, which are exported per year rather than as one
    combined parcels file -- a full all-years layer is 36 MB even simplified, so
    the export is deliberately split into purpose-built layers.
    """
    need(config.FOOTPRINT_BY_YEAR)
    import geopandas as gpd

    expected_by_year = pd.read_csv(config.FOOTPRINT_BY_YEAR).set_index("survey_year")
    checked = []
    for year in (2000, 2025):
        geo = config.TABLEAU_DIR / f"parcels_{year}.geojson"
        if not geo.exists():
            continue
        got = gpd.read_file(geo).to_crs(epsg=config.CRS_NZTM).geometry.area.sum() / 10_000
        expected = expected_by_year.loc[year, "naive_sum_ha"]
        loss_pct = abs(got - expected) / expected * 100
        assert loss_pct < MAX_SIMPLIFY_AREA_LOSS_PCT, (
            f"simplification moved {year} area by {loss_pct:.2f}% "
            f"({got:.1f} vs {expected:.1f} ha) -- reduce the tolerance"
        )
        checked.append(year)

    if not checked:
        skip("no parcel snapshot layers exported yet")


def test_new_plantings_export_retains_its_area_share():
    """
    The map layer drops sub-hectare pieces for file size. That is fine only
    because they carry almost no area -- verify the dropped share stays small,
    so the map doesn't quietly stop matching the numbers beside it.
    """
    geo = config.TABLEAU_DIR / "new_plantings.geojson"
    need(geo, config.TRANSITIONS)
    import geopandas as gpd

    exported = gpd.read_file(geo).to_crs(epsg=config.CRS_NZTM).geometry.area.sum() / 10_000
    total_new = pd.read_csv(config.TRANSITIONS)["new_ha"].sum()
    retained = exported / total_new * 100
    assert retained > 90.0, (
        f"new_plantings.geojson retains only {retained:.1f}% of total new area "
        f"({exported:,.0f} of {total_new:,.0f} ha) -- the size filter is too "
        f"aggressive for the map to represent the figures"
    )


def test_tableau_csv_join_keys_are_consistent():
    need(config.TABLEAU_DIR)
    csvs = sorted(config.TABLEAU_DIR.glob("*.csv"))
    if not csvs:
        skip("no Tableau CSV exports yet")
    for f in csvs:
        df = pd.read_csv(f)
        assert "survey_year" in df.columns, f"{f.name} has no survey_year join key"
        assert df["survey_year"].notna().all(), f"{f.name} has null survey_year"
        assert df["survey_year"].dtype.kind in "iu", (
            f"{f.name} survey_year must be integer, got {df['survey_year'].dtype}"
        )


# =========================================================================
# 3. Document fact-check
# =========================================================================
# Registry of headline claims. Each entry: how to compute the true value from
# the pipeline's own outputs, and which documents must quote it correctly.
#
# This is a deliberate contract. If a document legitimately drops a claim, remove
# it here; if a number drifts, the test fails and one of the two is wrong.

# Figures must be correct wherever they are quoted, internal docs included.
CHECKED_DOCS = ["PLAN_OF_ACTION.md", "REPORT.md"]

# The editorial guard applies only to publication surfaces. Internal working
# docs have to be able to *discuss* the constraint -- CLAUDE.md and
# PLAN_OF_ACTION.md both describe the banned phrasing in order to document the
# rule, and scoping by document is cleaner than trying to tell a claim apart
# from a description of a claim by pattern.
PUBLICATION_DOCS = ["REPORT.md"]


def _headline_figures() -> dict[str, float]:
    """
    Every figure a document is allowed to quote without the suite noticing drift.

    Deliberately covers more than the churn totals: REPORT.md quotes carrying
    capacity estimates, their interval bounds, and the retirement bands, and
    those are exactly the numbers most likely to move when a model or threshold
    is retuned. A claim that the suite fact-checks the report is only honest if
    the suite actually reaches them.
    """
    t = pd.read_csv(config.TRANSITIONS)
    fp = pd.read_csv(config.FOOTPRINT_BY_YEAR).set_index("survey_year")
    figures = {
        "gross new planted (ha)": t["new_ha"].sum(),
        "gross retired (ha)": t["retired_ha"].sum(),
        "net change (ha)": t["net_change_ha"].sum(),
        "2025 footprint (ha)": fp.loc[2025, "footprint_ha"],
        "churn ratio": t["new_ha"].sum() / t["net_change_ha"].sum(),
    }

    if config.MODEL_FITS.exists():
        fits = json.loads(config.MODEL_FITS.read_text())
        for name, f in fits.get("primary", {}).items():
            if not isinstance(f, dict) or not f.get("converged"):
                continue
            figures[f"{name} K (ha)"] = f["K_ha"]
            ci = f.get("K_ci95_ha") or f.get("K_ci95_withheld", {}).get("computed")
            if ci:
                figures[f"{name} K upper bound (ha)"] = ci[1]

    classification = config.STATS_DIR / "retirement_classification.csv"
    if classification.exists():
        band = pd.read_csv(classification)
        figures["likely genuine removal (ha)"] = band["likely_genuine_removal"].sum()
        figures["likely measurement change (ha)"] = band["likely_measurement_change"].sum()

    return figures


def _formattings(value: float) -> set[str]:
    """Spellings of a number a document might legitimately use."""
    out = set()
    for dp in (0, 1, 2):
        plain = f"{value:.{dp}f}"
        out.add(plain)
        out.add(f"{value:,.{dp}f}")
    # allow a trailing ".0" to be dropped, e.g. 1.27 vs 1.3
    return {s for s in out if s}


# Which figures each document is on the hook for. Scope matters: REPORT.md is
# the publication surface and must carry every headline number correctly, while
# PLAN_OF_ACTION.md is a working log that legitimately never discusses the
# saturation fits. Demanding every figure from every document would force
# irrelevant numbers into a doc just to satisfy a test.
CORE_FIGURES = {
    "gross new planted (ha)", "gross retired (ha)", "net change (ha)",
    "2025 footprint (ha)", "churn ratio",
}
DOC_SCOPE = {
    "REPORT.md": None,               # None == every figure
    "PLAN_OF_ACTION.md": CORE_FIGURES,
}


def test_documents_quote_the_computed_figures():
    need(config.TRANSITIONS, config.FOOTPRINT_BY_YEAR)
    figures = _headline_figures()

    present = [d for d in CHECKED_DOCS if (config.BASE_DIR / d).exists()]
    if not present:
        skip("no checked documents exist yet")

    problems = []
    for doc in present:
        text = (config.BASE_DIR / doc).read_text()
        scope = DOC_SCOPE.get(doc, CORE_FIGURES)
        for label, value in figures.items():
            if scope is not None and label not in scope:
                continue
            if not any(f in text for f in _formattings(value)):
                problems.append(
                    f"{doc} does not quote {label} correctly "
                    f"(computed {value:,.2f}; expected one of "
                    f"{sorted(_formattings(value))[:4]}...)"
                )
    assert not problems, (
        "Document figures disagree with the pipeline:\n  " + "\n  ".join(problems)
    )


def test_documents_do_not_claim_retired_land_is_vine_removal():
    """
    Guards the project's main open question. Until digitisation-forensics
    establishes the split, the retired figure must not be presented to a reader
    as vines actually pulled out. See PLAN_OF_ACTION.md.

    Scoped to PUBLICATION_DOCS: internal docs legitimately quote the banned
    phrasing when documenting this very rule.
    """
    forensics = config.STATS_DIR / "retirement_classification.csv"
    if forensics.exists():
        skip("forensics has run; the banded claim is now permitted")

    banned = re.compile(
        r"(vines?\s+(were\s+)?(pulled|removed|ripped)"
        r"|hectares?\s+of\s+vines?\s+removed"
        r"|\bgrubbed\b)",
        re.IGNORECASE,
    )
    offenders = []
    for doc in PUBLICATION_DOCS:
        p = config.BASE_DIR / doc
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if banned.search(line) and "plausibly" not in line.lower():
                offenders.append(f"{doc}:{i}: {line.strip()[:110]}")
    assert not offenders, (
        "Retired area presented as confirmed vine removal before the "
        "digitisation/removal split was established:\n  " + "\n  ".join(offenders)
    )


def test_model_fits_json_is_wellformed_if_present():
    need(config.MODEL_FITS)
    fits = json.loads(config.MODEL_FITS.read_text())
    assert isinstance(fits, dict) and fits, "model_fits.json is empty"
    # With n=18 a bare point estimate for carrying capacity is not publishable.
    for name, fit in fits.items():
        if not isinstance(fit, dict):
            continue
        if "K" in fit or "carrying_capacity" in fit:
            has_ci = any(k for k in fit if "ci" in k.lower() or "interval" in k.lower())
            assert has_ci, (
                f"model '{name}' reports a carrying capacity with no confidence "
                f"interval -- n=18 does not support a point estimate"
            )


if __name__ == "__main__":
    sys.exit(run_tests(globals(), "Pipeline invariants"))
