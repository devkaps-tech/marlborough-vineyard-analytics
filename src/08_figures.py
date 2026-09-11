"""
Step 8: Regenerate every report figure.

Replaces the ad-hoc PNGs that used to sit in data/processed/ unreproducibly.
Figures land in reports/figures/, which IS tracked in git (unlike data/) because
they are the case study's evidence.

Charts are built to the project's dataviz rules: categorical hues assigned in
fixed order and never cycled, one axis per chart (never a dual axis), sequential
for magnitude and diverging for polarity, thin marks, recessive grid, direct
labels rather than a number on every point.

Palette: reference categorical slots 1-3 (blue / orange / aqua), used unchanged
because that subset is documented as clearing all-pairs CVD separation in both
light and dark. Aqua sits below 3:1 on a light surface, so wherever it carries
meaning it also gets a direct label -- the relief rule.

Reads:  footprint_by_year.csv, transitions.csv, transitions_by_subregion.csv,
        stats/model_fits.json, stats/growth_phases.csv, stats/size_distribution.csv
Writes: reports/figures/*.png

Usage:
    python3 src/08_figures.py
"""
import json
import sys
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # noqa: E402  headless; must precede pyplot
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    FIGURES_DIR,
    FOOTPRINT_BY_YEAR,
    MODEL_FITS,
    STATS_DIR,
    TRANSITIONS,
    TRANSITIONS_SUBREGION,
    ensure_dirs,
    require,
)

# --- design tokens -------------------------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e3e2de"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"   # blue, orange, aqua
DIVERGE_POS, DIVERGE_NEG = "#2a78d6", "#d03b3b"
NEUTRAL = "#b9b8b3"

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK_2,
    "text.color": INK,
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
})


def style(ax, title, subtitle=None, ylabel=None):
    """
    Common chrome: recessive spines, title above, subtitle as the caption.

    The subtitle is wrapped and the title padded to clear it. These captions
    carry the analytical caveats, so they run long by design -- letting them
    collide with the title or overflow the axes would lose the very thing they
    exist to say.
    """
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)

    lines = textwrap.wrap(subtitle, width=92) if subtitle else []
    ax.set_title(title, fontsize=13, fontweight="bold", color=INK, loc="left",
                 pad=14 + 13 * len(lines))
    if lines:
        ax.text(0, 1.015, "\n".join(lines), transform=ax.transAxes, fontsize=9,
                color=INK_2, ha="left", va="bottom", linespacing=1.45)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(axis="x", visible=False)


def save(fig, name):
    path = FIGURES_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {name:34s} {path.stat().st_size / 1024:6.1f} KB")


# --- figures -------------------------------------------------------------

def fig_growth_curve(fp, fits):
    """
    The headline chart -- and it reports a NEGATIVE result honestly.

    Carrying capacity is not identified: the fitted K values sit above the
    observed footprint, but their 95% intervals run to a quarter of a million
    hectares. Drawing a single confident asymptote would be the lie. So the
    curves are drawn, the point estimates labelled, and the interval stated in
    text rather than as a plottable line -- because an interval that wide has no
    honest visual scale.
    """
    fig, ax = plt.subplots(figsize=(9, 5.4))
    yr = fp["survey_year"].to_numpy()
    ha = fp["footprint_ha"].to_numpy()

    primary = fits.get("primary", {})
    t0 = fits.get("meta", {}).get("time_origin", 2000)
    clean_start = 2006  # primary fit uses surveys after 2005
    grid = np.linspace(clean_start, yr.max() + 10, 300)
    tg = grid - t0

    notes = []
    for name, colour, dash in (("logistic", S2, (4, 3)), ("gompertz", S3, (1, 2.5))):
        f = primary.get(name, {})
        if not isinstance(f, dict) or not f.get("converged"):
            continue
        p = f["params"]
        curve = (p["K"] / (1 + np.exp(-p["r"] * (tg - p["t_mid"]))) if name == "logistic"
                 else p["K"] * np.exp(-np.exp(-p["b"] * (tg - p["t_mid"]))))
        ax.plot(grid, curve, color=colour, lw=1.6, dashes=dash, zorder=2,
                label=f"{name} fit (post-2005)")
        ci = (f.get("K_ci95_ha")
              or f.get("K_ci95_withheld", {}).get("computed"))
        notes.append(f"{name}: K = {p['K']:,.0f} ha"
                     + (f"   95% interval {ci[0]:,.0f} – {ci[1]:,.0f} ha" if ci else ""))

    # shade the surveys excluded from the primary fit
    ax.axvspan(yr.min() - 1, clean_start, color=NEUTRAL, alpha=0.16, zorder=0)
    ax.text(yr.min() + 0.4, ha.max() * 0.94,
            "2000–2005 excluded:\ndigitisation-distorted",
            fontsize=8, color=INK_2, va="top")

    ax.plot(yr, ha, color=S1, lw=2, zorder=4, label="Observed footprint")
    ax.scatter(yr, ha, s=26, color=S1, zorder=5, edgecolor=SURFACE, linewidth=1.5)

    ax.annotate(f"2025 observed\n{ha[-1]:,.0f} ha", xy=(yr[-1], ha[-1]),
                xytext=(yr[-1] - 9.5, ha[-1] * 0.56), fontsize=9, color=INK,
                arrowprops=dict(arrowstyle="->", color=INK_2, lw=1,
                                connectionstyle="arc3,rad=-0.15"))

    if notes:
        ax.text(0.02, 0.97, "\n".join(notes) +
                "\nThe intervals are too wide to call a limit — K is NOT identified.",
                transform=ax.transAxes, fontsize=8.5, color=INK_2,
                va="top", ha="left", linespacing=1.5)

    style(ax, "Planted vineyard footprint, 2000–2025",
          "Saturation models fitted to the clean post-2005 series. The data cannot "
          "distinguish 'approaching a limit' from 'still growing freely'",
          "hectares")
    ax.set_xlim(yr.min() - 1, grid.max() + 1)
    ax.set_ylim(0, ha.max() * 1.75)
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    save(fig, "growth_curve.png")


def fig_retirement_bands(classification):
    """
    The forensics split, drawn so its fragility is visible.

    The bands come from a 10 m erosion threshold, and that choice moves the
    answer a long way (86% genuine at 5 m, 38% at 40 m). The subtitle says so,
    because a stacked bar reads as settled fact unless told otherwise.
    """
    fig, ax = plt.subplots(figsize=(9, 4.8))
    d = classification.sort_values("survey_year")
    x = np.arange(len(d))

    genuine = d["likely_genuine_removal"].to_numpy()
    ambiguous = d["ambiguous"].to_numpy()
    measurement = d["likely_measurement_change"].to_numpy()

    ax.bar(x, genuine, color=S1, width=0.62, label="Likely genuine removal", zorder=3)
    ax.bar(x, ambiguous, bottom=genuine, color=NEUTRAL, width=0.62,
           label="Ambiguous", zorder=3)
    ax.bar(x, measurement, bottom=genuine + ambiguous, color=S2, width=0.62,
           label="Likely measurement change", zorder=3)

    # Call out 2011-12 specifically: it is the interval where the question
    # mattered most, and the answer is that the contraction survives the
    # measurement-change deduction rather than being explained away by it.
    totals = genuine + ambiguous + measurement
    mask = (d["prior_survey_year"] == 2011).to_numpy()
    if mask.any():
        i = int(np.flatnonzero(mask)[0])
        ax.annotate(
            f"Post-2011 contraction holds:\n{genuine[i]:,.0f} ha genuine after\n"
            f"removing {measurement[i]:,.0f} ha of noise",
            xy=(i, totals[i]), xytext=(i - 3.4, totals[i] * 1.02),
            fontsize=8.5, color=INK, linespacing=1.4,
            arrowprops=dict(arrowstyle="->", color=INK_2, lw=1))

    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(a)}–{int(b)}" for a, b in
                        zip(d["prior_survey_year"], d["survey_year"])],
                       rotation=45, ha="right", fontsize=8)
    style(ax, "Retired land: genuine removal vs measurement change",
          "Split at a 10 m erosion threshold. Highly sensitive — 86% genuine at 5 m, "
          "38% at 40 m — so treat the bands as a modelling choice, not a measurement",
          "hectares retired")
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    save(fig, "retirement_bands.png")


def fig_annualised_growth(phases):
    """Diverging by sign: growth vs contraction. Never a dual axis."""
    fig, ax = plt.subplots(figsize=(9, 4.4))
    d = phases.sort_values("survey_year")
    x = np.arange(len(d))
    vals = d["annualised_pct"].to_numpy()
    colours = [DIVERGE_POS if v >= 0 else DIVERGE_NEG for v in vals]

    ax.bar(x, vals, color=colours, width=0.62, zorder=3)
    ax.axhline(0, color=INK_2, lw=1, zorder=4)

    for xi, v in zip(x, vals):
        if abs(v) > 10 or v < 0:  # label only what carries the story
            ax.text(xi, v + (0.9 if v >= 0 else -1.6), f"{v:.1f}%",
                    ha="center", fontsize=8, color=INK)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(a)}–{int(b)}" for a, b in
                        zip(d["prior_year"], d["survey_year"])],
                       rotation=45, ha="right", fontsize=8)
    style(ax, "Annualised growth rate by survey interval",
          "Annualised because intervals are irregular — 2013–2018 is five years, "
          "so its raw +15.3% is only +2.9%/yr",
          "% per year")
    save(fig, "annualised_growth.png")


def fig_churn(trans):
    """
    Gross new vs gross retired per interval.

    Retired is drawn downward and captioned as unclassified: some of it is real
    vine removal, some is the same ground re-digitised. Presenting it as removal
    would be a claim the data does not yet support.
    """
    fig, ax = plt.subplots(figsize=(9, 4.8))
    d = trans.sort_values("survey_year")
    x = np.arange(len(d))

    ax.bar(x, d["new_ha"], color=S1, width=0.62, label="New planting", zorder=3)
    ax.bar(x, -d["retired_ha"], color=S2, width=0.62,
           label="Retired (unclassified)", zorder=3)
    ax.plot(x, d["net_change_ha"], color=INK, lw=1.6, marker="o", ms=4,
            label="Net change", zorder=5)
    ax.axhline(0, color=INK_2, lw=1, zorder=4)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(a)}–{int(b)}" for a, b in
                        zip(d["prior_survey_year"], d["survey_year"])],
                       rotation=45, ha="right", fontsize=8)
    style(ax, "Gross planting and retirement vs net change",
          "The published net series hides ~8,000 ha of land leaving production. "
          "Retired area is not yet separated into removal vs re-digitisation",
          "hectares per interval")
    ax.legend(frameon=False, fontsize=9, loc="upper right", ncols=3)
    ax.yaxis.set_major_formatter(lambda v, _: f"{abs(v):,.0f}")
    save(fig, "churn_gross_vs_net.png")


def fig_subregion(sub):
    """Two named sub-regions plus the minor pockets; direct-labelled at the end."""
    fig, ax = plt.subplots(figsize=(9, 4.8))
    piv = sub.pivot_table(index="survey_year", columns="subregion",
                          values="footprint_ha", aggfunc="sum").fillna(0).sort_index()

    order = [c for c in ["Wairau Valley / Southern Valleys", "Awatere Valley",
                         "Other / minor pocket (unverified)"] if c in piv.columns]
    colours = {order[i]: c for i, c in enumerate([S1, S2, S3][:len(order)])}

    ax.stackplot(piv.index, *[piv[c] for c in order],
                 colors=[colours[c] for c in order], labels=order,
                 edgecolor=SURFACE, linewidth=1.4)

    cum = 0
    for c in order:
        v = piv[c].iloc[-1]
        if v > 800:  # direct-label the readable bands (relief rule for aqua)
            ax.text(piv.index[-1] + 0.4, cum + v / 2,
                    f"{c.split(' /')[0]}\n{v:,.0f} ha",
                    fontsize=8.5, color=INK, va="center")
        cum += v

    style(ax, "Footprint by sub-region, 2000-2025",
          "Sub-regions are DBSCAN-derived, not official viticultural boundaries; "
          "Wairau proper and the Southern Valleys cannot be separated",
          "hectares")
    ax.set_xlim(piv.index.min(), piv.index.max() + 7)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    save(fig, "subregion_footprint.png")


def fig_size_diagnostic(sizes):
    """
    Explicitly a measurement diagnostic, not a finding about vineyard structure.
    The title says so, because this chart is the one most likely to be misread.
    """
    fig, ax = plt.subplots(figsize=(9, 4.2))
    d = sizes.sort_values("survey_year")
    ax.plot(d["survey_year"], d["mean_ha"], color=S1, lw=2, marker="o", ms=4.5,
            label="Mean parcel size")
    ax.plot(d["survey_year"], d["median_ha"], color=S2, lw=1.6, dashes=(4, 3),
            marker="s", ms=3.5, label="Median parcel size")

    ax.annotate(f"{d['mean_ha'].iloc[0]:.1f} ha",
                xy=(d["survey_year"].iloc[0], d["mean_ha"].iloc[0]),
                xytext=(6, 6), textcoords="offset points", fontsize=9, color=INK)
    ax.annotate(f"{d['mean_ha'].iloc[-1]:.1f} ha",
                xy=(d["survey_year"].iloc[-1], d["mean_ha"].iloc[-1]),
                xytext=(-10, 12), textcoords="offset points", fontsize=9, color=INK)

    style(ax, "Parcel size over time — a digitisation diagnostic, not fragmentation",
          "Falling mean size reflects finer polygon capture in later surveys. "
          "Do not read this as vineyards being subdivided",
          "hectares per parcel")
    ax.legend(frameon=False, fontsize=9)
    save(fig, "parcel_size_diagnostic.png")


def main():
    ensure_dirs()
    require(FOOTPRINT_BY_YEAR, TRANSITIONS, TRANSITIONS_SUBREGION, MODEL_FITS)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    fp = pd.read_csv(FOOTPRINT_BY_YEAR)
    trans = pd.read_csv(TRANSITIONS)
    sub = pd.read_csv(TRANSITIONS_SUBREGION)
    fits = json.loads(MODEL_FITS.read_text())

    print("Figures:")
    fig_growth_curve(fp, fits)

    phases = STATS_DIR / "growth_phases.csv"
    if phases.exists():
        fig_annualised_growth(pd.read_csv(phases))

    fig_churn(trans)

    classification = STATS_DIR / "retirement_classification.csv"
    if classification.exists():
        fig_retirement_bands(pd.read_csv(classification))

    fig_subregion(sub)

    sizes = STATS_DIR / "size_distribution.csv"
    if sizes.exists():
        fig_size_diagnostic(pd.read_csv(sizes))

    print(f"\nWrote {len(list(FIGURES_DIR.glob('*.png')))} figures to {FIGURES_DIR}")


if __name__ == "__main__":
    main()
