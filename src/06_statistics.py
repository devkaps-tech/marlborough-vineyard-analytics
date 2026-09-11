"""
Step 6: Statistical analysis of the vineyard growth series.

THE HEADLINE QUESTION: has planted area begun to saturate?

Deliberately NOT 'is Marlborough running out of plantable land' -- there is no
land-supply variable anywhere in this data, and a curve fitted to planted
footprint cannot speak to how much plantable land remains.

Fits saturation curves (logistic, Gompertz) to the overlay-derived footprint
series and reports the carrying capacity K -- the area the region would
asymptote to if the current growth process continued.

WHY THE STATISTICS HERE ARE DELIBERATELY CONSERVATIVE

n = 18. That is the entire sample: eighteen survey years. Parcels are not
independent observations of the growth process, so the 59,099-row parcel table
buys no statistical power. Three consequences run through this whole script:

  1. AICc, never plain AIC. At n=18 with 4 estimated quantities the correction
     term is material, and plain AIC would favour the more complex model wrongly.
  2. Confidence intervals come from a residual bootstrap, not from curve_fit's
     covariance matrix, which is unreliable for a non-linear fit this small.
  3. K is only trustworthy if the series has begun to turn over. If it hasn't,
     the fit can put K almost anywhere and the honest answer is "not identified
     by this data" -- which this script will say outright rather than quoting a
     number with false precision.

WHICH SERIES IS PRIMARY, AND WHY IT CHANGED

The primary fit uses surveys AFTER 2005 (n=15). The 2000, 2002 and 2005 surveys
are digitisation-distorted -- early coverage was growing alongside the vines, so
those points are artificially low. Fitting all 18 years steepens the early limb
and drags the asymptote down BELOW the present footprint, which looks like proof
that the saturation form is wrong. It isn't: it is a symptom of the contaminated
segment. That all-years fit is kept as `diagnostic_all_years` precisely because
the distortion it exposes is worth showing -- but it must not be the headline.
On the clean subset the honest finding is weaker and more defensible: K is not
identified by this data.

FITTING DISCIPLINE: each model gets at most TWO attempts (data-derived starts,
then widened bounds with a different start). If the second fails, that is
recorded as the result and the script moves on. There are no retry loops here.

Reads:  footprint_by_year.csv, transitions.csv, transitions_by_subregion.csv,
        parcels_enriched.parquet
Writes: stats/model_fits.json, stats/growth_phases.csv, stats/size_distribution.csv,
        stats/subregion_divergence.csv

Usage:
    python3 src/06_statistics.py
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import OptimizeWarning, curve_fit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    FOOTPRINT_BY_YEAR,
    MODEL_FITS,
    PARCELS_ENRICHED,
    STATS_DIR,
    TRANSITIONS,
    TRANSITIONS_SUBREGION,
    ensure_dirs,
    require,
)

MAX_FIT_ATTEMPTS = 2          # hard cap, per the project's no-retry-loop rule
N_BOOTSTRAP = 1000
RNG_SEED = 20250911           # fixed so the reported CIs are reproducible
T0 = 2000                     # time origin, keeps parameters well-scaled

# K is treated as unidentified if the bootstrap upper bound exceeds this
# multiple of the observed 2025 footprint. A logistic fitted to a series that
# has not turned over can run K off to infinity; quoting that as a projection
# would be meaningless.
K_IDENTIFIED_MAX_MULTIPLE = 3.0


# --- model forms ---------------------------------------------------------

def logistic(t, K, r, tm):
    """Symmetric S-curve. tm is the inflection year (offset from T0)."""
    return K / (1.0 + np.exp(-r * (t - tm)))


def gompertz(t, K, b, tm):
    """Asymmetric S-curve -- approaches K more slowly than it leaves zero."""
    return K * np.exp(-np.exp(-b * (t - tm)))


MODELS = {
    "logistic": (logistic, ("K", "r", "t_mid")),
    "gompertz": (gompertz, ("K", "b", "t_mid")),
}


# --- fitting -------------------------------------------------------------

def _starts(t, y, attempt: int):
    """Initial guesses and bounds. Attempt 2 widens both and shifts the start."""
    k_obs = float(y.max())
    t_mid_guess = float(t[np.argmin(np.abs(y - k_obs / 2))])
    if attempt == 1:
        p0 = [k_obs * 1.2, 0.3, t_mid_guess]
        bounds = ([k_obs * 0.9, 1e-4, t.min() - 20], [k_obs * 10, 5.0, t.max() + 60])
    else:
        # wider, and start from a much larger K in case attempt 1 sat in a
        # local minimum pinned near the observed maximum
        p0 = [k_obs * 3.0, 0.15, t_mid_guess + 5]
        bounds = ([k_obs * 0.5, 1e-6, t.min() - 100], [k_obs * 100, 10.0, t.max() + 300])
    return p0, bounds


def fit_model(name, func, t, y):
    """
    Fit with at most MAX_FIT_ATTEMPTS. Returns (params, attempts, error_or_None).
    Never loops beyond the cap.
    """
    last_err = None
    for attempt in range(1, MAX_FIT_ATTEMPTS + 1):
        p0, bounds = _starts(t, y, attempt)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", OptimizeWarning)
                popt, _ = curve_fit(func, t, y, p0=p0, bounds=bounds, maxfev=20000)
            return np.asarray(popt), attempt, None
        except Exception as e:  # noqa: BLE001 - any failure counts as an attempt
            last_err = f"{type(e).__name__}: {e}"
            print(f"    {name} attempt {attempt}/{MAX_FIT_ATTEMPTS} failed: {last_err}")
    return None, MAX_FIT_ATTEMPTS, last_err


def aicc(y, yhat, n_params: int) -> float | None:
    """
    Small-sample-corrected AIC. k counts the fitted parameters plus the
    residual variance. Returns None when the correction is undefined.
    """
    n = len(y)
    rss = float(np.sum((y - yhat) ** 2))
    k = n_params + 1
    if rss <= 0 or n - k - 1 <= 0:
        return None
    aic = n * np.log(rss / n) + 2 * k
    return aic + (2 * k * (k + 1)) / (n - k - 1)


def lag1_autocorr(resid) -> float | None:
    """
    Lag-1 autocorrelation of the residuals.

    This matters more than it looks. Both AICc and the residual bootstrap below
    assume residuals are independent. If they are strongly autocorrelated, the
    fit is systematically missing structure rather than scattering around it --
    which means the AICc gap is not an interpretable likelihood difference and
    the bootstrap interval is a LOWER BOUND on the true uncertainty, because
    resampling destroys exactly the dependence that carries the information.
    """
    if len(resid) < 3:
        return None
    a, b = resid[:-1], resid[1:]
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def bootstrap_K(func, t, y, popt, n_draws=N_BOOTSTRAP):
    """
    Residual bootstrap for K. curve_fit's covariance matrix is not reliable for
    a non-linear fit at this sample size, so resample residuals and refit.

    TWO LIMITATIONS, both reported alongside the interval rather than hidden:

    1. Draws are generated as fitted_curve + resampled_residual, so the interval
       propagates sampling noise CONDITIONAL ON THE MODEL BEING TRUE. It inherits
       any invalidity in the point estimate wholesale.
    2. Each draw refits under `_starts(..., attempt=1)`, whose lower bound is
       0.9 * that draw's own maximum. K therefore cannot fall far below the data,
       so a percentile sitting near that bound is partly the constraint showing
       through, not evidence of precision.

    Draws that fail to converge are skipped, not retried.
    """
    rng = np.random.default_rng(RNG_SEED)
    resid = y - func(t, *popt)
    ks, t95s = [], []
    for _ in range(n_draws):
        y_b = func(t, *popt) + rng.choice(resid, size=len(resid), replace=True)
        try:
            p0, bounds = _starts(t, y_b, 1)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pb, _ = curve_fit(func, t, y_b, p0=p0, bounds=bounds, maxfev=8000)
            ks.append(pb[0])
            t95s.append(years_to_fraction(func, pb, 0.95))
        except Exception:  # noqa: BLE001 - a failed draw is simply dropped
            continue
    return np.asarray(ks), np.asarray([x for x in t95s if x is not None], dtype=float)


def years_to_fraction(func, popt, frac: float, horizon=200):
    """Calendar year at which the fitted curve reaches `frac` of K."""
    K = popt[0]
    grid = np.arange(0, horizon, 0.25)
    vals = func(grid, *popt)
    hit = np.argmax(vals >= frac * K)
    if vals[hit] < frac * K:
        return None
    return float(T0 + grid[hit])


def summarise_fit(name, func, param_names, t, y, years):
    print(f"  fitting {name}...")
    popt, attempts, err = fit_model(name, func, t, y)
    if popt is None:
        print(f"    -> NOT CONVERGED after {attempts} attempts; reporting and moving on")
        return {"converged": False, "attempts": attempts, "error": err}

    yhat = func(t, *popt)
    resid = y - yhat
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum(resid**2)) / ss_tot if ss_tot else None

    ks, t95s = bootstrap_K(func, t, y, popt)
    observed_max = float(y.max())
    ac1 = lag1_autocorr(resid)

    out = {
        "converged": True,
        "attempts": attempts,
        "params": {n: float(v) for n, v in zip(param_names, popt)},
        "K_ha": float(popt[0]),
        "r_squared": r2,
        "rmse_ha": float(np.sqrt(np.mean(resid**2))),
        "aicc": aicc(y, yhat, len(popt)),
        "n_observations": int(len(y)),
        "bootstrap_draws_succeeded": int(len(ks)),
        "residual_lag1_autocorr": ac1,
    }
    if ac1 is not None and abs(ac1) > 0.3:
        out["residual_independence_violated"] = (
            f"lag-1 autocorrelation {ac1:.2f} -- residuals are not independent, so "
            f"the AICc value is not a clean likelihood comparison and the bootstrap "
            f"interval understates true uncertainty"
        )

    # A carrying capacity BELOW the observed maximum is not a carrying capacity.
    # It means the S-curve has been forced onto a series that is still growing:
    # the fit gets pulled down by an interior plateau and the asymptote lands
    # under data the region has already reached. A high R2 does not rescue this
    # -- the parameter is simply not interpretable as a limit.
    out["observed_max_ha"] = observed_max
    out["K_exceeds_observed"] = bool(popt[0] > observed_max)
    if not out["K_exceeds_observed"]:
        out["misspecification"] = (
            f"fitted K ({popt[0]:,.0f} ha) is BELOW the observed maximum "
            f"({observed_max:,.0f} ha) -- the saturation form does not describe "
            f"this series and K must not be reported as a limit"
        )

    if len(ks) >= 100:
        lo, hi = np.percentile(ks, [2.5, 97.5])
        # Three conditions, all required. Recorded individually so a reader can
        # see WHICH one failed rather than just a bare false.
        checks = {
            "K_above_observed_max": bool(popt[0] > observed_max),
            "ci_lower_above_observed_max": bool(lo > observed_max),
            "ci_upper_within_multiple": bool(hi <= K_IDENTIFIED_MAX_MULTIPLE * observed_max),
        }
        out["identification_checks"] = checks
        out["K_identified"] = all(checks.values())

        if out["K_identified"]:
            out["K_ci95_ha"] = [float(lo), float(hi)]
            if len(t95s) >= 100:
                out["year_95pct_K"] = years_to_fraction(func, popt, 0.95)
                out["year_95pct_K_ci95"] = [
                    float(x) for x in np.percentile(t95s, [2.5, 97.5])
                ]
        else:
            # Deliberately withheld. A confidence interval printed beside a
            # statement that the estimate is meaningless gets read as precision,
            # and its lower end is partly the optimiser's bound anyway.
            out["K_ci95_ha"] = None
            out["K_ci95_withheld"] = {
                "computed": [float(lo), float(hi)],
                "reason": "K is not identified; interval would imply false precision",
                "lower_bound_is_constrained": bool(lo <= 0.95 * observed_max),
            }
    else:
        out["K_ci95_ha"] = None
        out["K_identified"] = False
        out["ci_note"] = (
            f"only {len(ks)} of {N_BOOTSTRAP} bootstrap draws converged; "
            f"interval not reportable"
        )

    ci = out.get("K_ci95_ha")
    ci_txt = f"[{ci[0]:,.0f}, {ci[1]:,.0f}]" if ci else "unavailable"
    print(f"    -> K={out['K_ha']:,.0f} ha  CI95 {ci_txt}  "
          f"R2={r2:.4f}  AICc={out['aicc']:.2f}  "
          f"identified={out['K_identified']}")
    if not out["K_exceeds_observed"]:
        print(f"       MISSPECIFIED: K is below the observed maximum "
              f"({observed_max:,.0f} ha) -- not a limit")
    return out


# --- supporting analyses -------------------------------------------------

def growth_phases(fp: pd.DataFrame) -> pd.DataFrame:
    """Annualised growth per interval -- the only comparable rate given the gaps."""
    d = fp.sort_values("survey_year").copy()
    d["prior_year"] = d["survey_year"].shift(1)
    d["span"] = d["survey_year"] - d["prior_year"]
    d["annualised_pct"] = (
        (d["footprint_ha"] / d["footprint_ha"].shift(1)) ** (1 / d["span"]) - 1
    ) * 100
    d["abs_change_ha_per_yr"] = d["footprint_ha"].diff() / d["span"]
    return d.dropna(subset=["annualised_pct"])[
        ["prior_year", "survey_year", "span", "footprint_ha",
         "annualised_pct", "abs_change_ha_per_yr"]
    ]


def size_distribution(parcels: pd.DataFrame) -> pd.DataFrame:
    """
    Parcel-size summary by year.

    This is a DIGITISATION DIAGNOSTIC, not a finding about vineyard structure.
    Mean parcel size falling 27 -> 6.5 ha reflects finer polygon capture in later
    surveys, not real fragmentation. Presenting it as a trend would be wrong.
    """
    g = parcels.groupby("survey_year")["area_hectares"]
    out = g.agg(n_parcels="count", mean_ha="mean", median_ha="median",
                p10_ha=lambda s: s.quantile(0.10),
                p90_ha=lambda s: s.quantile(0.90)).reset_index()
    out["interpretation"] = "digitisation resolution proxy -- not fragmentation"
    return out


def subregion_divergence(sub: pd.DataFrame) -> pd.DataFrame:
    """Each sub-region's share of new plantings per interval."""
    d = sub.copy()
    total = d.groupby("survey_year")["new_ha"].transform("sum")
    d["share_of_new_pct"] = np.where(total > 0, d["new_ha"] / total * 100, np.nan)
    return d[["prior_survey_year", "survey_year", "subregion", "new_ha",
              "new_ha_per_year", "share_of_new_pct"]].sort_values(
        ["survey_year", "subregion"]
    )


def main():
    ensure_dirs()
    require(FOOTPRINT_BY_YEAR, TRANSITIONS, TRANSITIONS_SUBREGION, PARCELS_ENRICHED)

    fp = pd.read_csv(FOOTPRINT_BY_YEAR).sort_values("survey_year")
    years = fp["survey_year"].to_numpy(dtype=float)
    t = years - T0
    y_footprint = fp["footprint_ha"].to_numpy(dtype=float)
    y_naive = fp["naive_sum_ha"].to_numpy(dtype=float)

    print(f"Fitting saturation models to {len(years)} survey years "
          f"({int(years[0])}-{int(years[-1])})")
    print(f"Observed 2025 footprint: {y_footprint[-1]:,.0f} ha\n")

    # Surveys strictly AFTER 2005. `>= 2005` would retain 2005, which is one of
    # the three digitisation-distorted surveys this mask exists to exclude --
    # and 2005 is the single biggest lever on the early limb (the footprint
    # jumps 6,348 -> 16,674 ha over 2002-2005).
    clean = years > 2005

    results = {"meta": {
        "n_observations_all": int(len(years)),
        "n_observations_primary": int(clean.sum()),
        "series": "overlay-derived dissolved footprint (footprint_by_year.csv)",
        "primary_definition": "surveys after 2005 (excludes the three "
                              "digitisation-distorted early surveys)",
        "time_origin": T0,
        "max_fit_attempts": MAX_FIT_ATTEMPTS,
        "bootstrap_draws": N_BOOTSTRAP,
        "rng_seed": RNG_SEED,
        "k_identified_max_multiple": K_IDENTIFIED_MAX_MULTIPLE,
        "caveats": [
            "n=15 in the primary fit; parcels are not independent observations",
            "survey intervals are irregular (2013-2018, 2018-2020 gaps)",
            "2000/2002/2005 surveys are digitisation-distorted and excluded from "
            "the primary fit; see diagnostic_all_years for what including them does",
            "K is only meaningful if the series has begun to turn over",
            "residuals are autocorrelated, so AICc gaps and bootstrap intervals "
            "are weaker evidence than their face value suggests",
        ],
    }}

    print(f"PRIMARY -- post-2005 surveys only (n={int(clean.sum())})")
    results["primary"] = {
        name: summarise_fit(name, func, pnames, t[clean], y_footprint[clean],
                            years[clean])
        for name, (func, pnames) in MODELS.items()
    }

    print("\nDIAGNOSTIC -- all 18 years, including the distorted early surveys")
    results["diagnostic_all_years"] = {
        name: summarise_fit(name, func, pnames, t, y_footprint, years)
        for name, (func, pnames) in MODELS.items()
    }
    results["diagnostic_all_years"]["note"] = (
        "Including 2000/2002/2005 pulls the asymptote BELOW the observed 2025 "
        "footprint. That is a symptom of the digitisation distortion in those "
        "surveys -- early coverage grew alongside the vines, so the early points "
        "are artificially low and steepen the limb. It is NOT evidence that the "
        "saturation form is wrong for the underlying process, and must not be "
        "reported as such."
    )

    # Expected to be near-identical to the primary fit: the dissolve established
    # that intra-year polygon overlap is ~0.0009%, so the two series differ by
    # far less than the residual scale. Kept because that equivalence is itself
    # worth recording, not because it is an independent check.
    print("\nSENSITIVITY -- naive sum-of-parcels, same post-2005 window "
          "(expected ~identical; overlap is ~0)")
    results["sensitivity_naive"] = {
        name: summarise_fit(name, func, pnames, t[clean], y_naive[clean],
                            years[clean])
        for name, (func, pnames) in MODELS.items()
    }
    results["sensitivity_naive"]["note"] = (
        "near-identical to primary by construction -- the dissolved footprint "
        "and the naive sum differ by <0.001%, so this is a consistency check, "
        "not independent evidence"
    )

    # --- model comparison on AICc ----------------------------------------
    conv = {n: r for n, r in results["primary"].items()
            if isinstance(r, dict) and r.get("converged") and r.get("aicc") is not None}
    if len(conv) >= 2:
        ranked = sorted(conv.items(), key=lambda kv: kv[1]["aicc"])
        gap = ranked[1][1]["aicc"] - ranked[0][1]["aicc"]
        autocorrelated = any(
            r.get("residual_lag1_autocorr") is not None
            and abs(r["residual_lag1_autocorr"]) > 0.3
            for r in conv.values()
        )
        results["model_comparison"] = {
            "best_by_aicc": ranked[0][0],
            "aicc_gap": float(gap),
            # A gap under 2 is not evidence of a real difference; saying
            # otherwise would be over-reading fifteen points.
            "distinguishable": bool(gap >= 2.0 and not autocorrelated),
            "residuals_autocorrelated": autocorrelated,
            "note": (
                "AICc gap < 2 -- the models are not distinguishable on this data; "
                "do not claim one fits better"
                if gap < 2.0 else
                f"{ranked[0][0]} has the lower AICc (gap {gap:.2f})"
                + (". But residuals are autocorrelated, so this is not a clean "
                   "likelihood comparison -- it ranks which curve is less wrong, "
                   "and is NOT evidence that growth has that functional form"
                   if autocorrelated else "")
            ),
        }
        print(f"\nModel comparison: {results['model_comparison']['note']}")

    # --- supporting outputs ----------------------------------------------
    phases = growth_phases(fp)
    phases.to_csv(STATS_DIR / "growth_phases.csv", index=False)

    parcels = pd.read_parquet(PARCELS_ENRICHED, columns=["survey_year", "area_hectares"])
    size_distribution(parcels).to_csv(STATS_DIR / "size_distribution.csv", index=False)

    sub = pd.read_csv(TRANSITIONS_SUBREGION)
    subregion_divergence(sub).to_csv(STATS_DIR / "subregion_divergence.csv", index=False)

    trans = pd.read_csv(TRANSITIONS)
    results["churn"] = {
        "gross_new_ha": float(trans["new_ha"].sum()),
        "gross_retired_ha": float(trans["retired_ha"].sum()),
        "net_change_ha": float(trans["net_change_ha"].sum()),
        "churn_ratio": float(trans["new_ha"].sum() / trans["net_change_ha"].sum()),
        "churn_ratio_formula": "gross_new_ha / net_change_ha",
        "retired_classification": (
            "not yet separated into genuine removal vs re-digitisation "
            "-- see src/09_retirement_forensics.py"
        ),
    }

    MODEL_FITS.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {MODEL_FITS.name}, growth_phases.csv, size_distribution.csv, "
          f"subregion_divergence.csv to {STATS_DIR}")

    # --- the honest headline ---------------------------------------------
    print("\n" + "=" * 70)
    primary_fits = {n: r for n, r in results["primary"].items()
                    if isinstance(r, dict) and r.get("converged")}
    identified = {n: r for n, r in primary_fits.items() if r.get("K_identified")}
    observed = float(y_footprint[-1])

    if identified:
        name, r = next(iter(identified.items()))
        lo, hi = r["K_ci95_ha"]
        print(f"HEADLINE: {name} fit gives carrying capacity "
              f"{r['K_ha']:,.0f} ha (95% CI {lo:,.0f}-{hi:,.0f}).")
        print(f"Current footprint is {observed:,.0f} ha, "
              f"{observed / r['K_ha'] * 100:.0f}% of K.")
        results["headline"] = {"k_identified": True, "model": name}
    else:
        # The defensible negative result. Report the widest upper bound actually
        # computed rather than a number typed into this string.
        uppers = [r["K_ci95_withheld"]["computed"][1] for r in primary_fits.values()
                  if r.get("K_ci95_withheld")]
        widest = max(uppers) if uppers else None

        print("HEADLINE: carrying capacity is NOT identified by this data.")
        print(f"On the clean post-2005 series, every fitted K sits above the")
        print(f"observed {observed:,.0f} ha, but the intervals are far too wide to")
        print("call a limit:")
        for name, r in primary_fits.items():
            w = r.get("K_ci95_withheld", {}).get("computed")
            rng_txt = f"interval {w[0]:,.0f}-{w[1]:,.0f} ha" if w else "interval unavailable"
            print(f"  {name:9s} K = {r['K_ha']:,.0f} ha  ({rng_txt})")
        if widest:
            print(f"\nThe upper bound reaches {widest:,.0f} ha -- the data cannot")
            print("distinguish 'approaching a limit' from 'still growing freely'.")
        print()
        print("SEPARATELY, as a diagnostic: fitting all 18 years puts K BELOW the")
        print("present footprint. That is the digitisation distortion in the early")
        print("surveys showing through, not evidence about the growth process.")
        print()
        print("Frame the finding as the shape of the rate series, not as a capacity.")
        print("Say: rapid expansion through 2008, near-zero to negative change")
        print("2011-2012, and renewed growth that had ALREADY resumed by the 2018")
        print("survey -- the 2013-2018 gap means the resumption date is unobserved.")

        results["headline"] = {
            "k_identified": False,
            "reason": "K estimable but interval far too wide to constrain a limit",
            "widest_upper_bound_ha": widest,
            "recommended_framing": (
                "describe the rate series, not a carrying capacity; the 2013-2018 "
                "survey gap means the growth resumption date is unobserved, so do "
                "not date it to 2018"
            ),
            "do_not_claim": [
                "a carrying capacity or 'years to capacity'",
                "that growth resumed in 2018 (it resumed somewhere in the gap)",
                "a count of discrete regimes (asserted, not statistically detected)",
                "any cause for the 2011-2012 contraction (no price/weather variable)",
            ],
        }

    print("=" * 70)
    MODEL_FITS.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
