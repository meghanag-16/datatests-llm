"""Results dashboard (black and white).

  uv run streamlit run dashboard/app.py

Reads dashboard/results.json; rebuild it from reports/ with: uv run python dashboard/export.py
"""
from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

DATA = json.loads((Path(__file__).resolve().parent / "results.json").read_text(encoding="utf-8"))
BLACK, WHITE = "#000000", "#FFFFFF"

st.set_page_config(page_title="datatests-llm results", layout="centered")
st.markdown(
    """<style>
    html, body, [class*="st-"], .stApp { color: #000; background: #fff; }
    h1, h2, h3 { font-weight: 600; letter-spacing: -0.01em; }
    [data-testid="stMetric"] { border: 1px solid #000; padding: 12px 14px; }
    [data-testid="stMetricValue"] { font-size: 1.9rem; }
    hr { border-color: #000 !important; background: #000; }
    [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p, small { color: #000 !important; }
    [data-testid="stTooltipIcon"] svg { color: #000; }
    </style>""",
    unsafe_allow_html=True,
)


def style(chart: alt.Chart, height: int = 220) -> alt.Chart:
    """Black and white only: black marks, black text, no grid."""
    return (chart.properties(height=height, background=WHITE)
            .configure_view(stroke=None)
            .configure_axis(domainColor=BLACK, tickColor=BLACK, labelColor=BLACK, titleColor=BLACK,
                            grid=False, labelFontSize=12, titleFontSize=12, titleFontWeight="normal")
            .configure_legend(labelColor=BLACK, titleColor=BLACK, orient="top", title=None,
                              labelFontSize=12, labelLimit=0, symbolStrokeColor=BLACK)
            .configure_text(color=BLACK))


def two_tone(field: str, order: list[str]) -> dict:
    """First series filled black, second white with a black outline."""
    return dict(color=alt.Color(f"{field}:N", sort=order, scale=alt.Scale(domain=order, range=[BLACK, WHITE])))


def show(chart: alt.Chart, height: int = 220) -> None:
    st.altair_chart(style(chart, height), width="stretch", theme=None)


scoring = pd.DataFrame(DATA["scoring"])
scoring["run"] = scoring["dataset"] + " · " + scoring["llm"]
repro = DATA["reproduction"]
olist = scoring[scoring["dataset"] == "Olist"]
football = scoring[scoring["dataset"] == "Football"]

# ------------------------------------------------------------------ header
st.title("LLM-generated dbt tests")
st.write("Generate dbt data tests with an LLM, damage copies of the data in known ways, and keep only the "
         "tests that catch something. Reproduces Gargouri & Reza (arXiv:2605.20500) and extends it with "
         "mutation scoring, pruning and a held-out evaluation on two datasets.")

c1, c2, c3 = st.columns(3)
c1.metric("Paper result reproduced", f"{repro['rows'][0]['detected']} → {repro['rows'][1]['detected']} / {repro['total']}",
          help="Anomalies caught: manual tests only, then manual expanded.")
removable = 1 - football["minimal_set"].sum() / football["working_tests"].sum()
c2.metric("Tests removable (football)", f"{removable:.0%}",
          help="Share of working tests a minimal set can drop with no loss of detection.")
kept = olist["heldout_fixed"].map(lambda h: h["kept_pct_of_full"])
c3.metric("Unseen damage kept (Olist)", f"{kept.min():.1f}–{kept.max():.1f}%",
          help="Share of what the full test set catches on mutation types the minimal set was not chosen on.")

st.divider()

# ------------------------------------------------------------------ 1 reproduction
st.subheader("1. Reproduction")
rows = pd.DataFrame(repro["rows"])
bars = alt.Chart(rows).mark_bar(color=BLACK, size=18).encode(
    x=alt.X("detected:Q", title=f"Anomalies caught (of {repro['total']})", scale=alt.Scale(domain=[0, repro["total"]])),
    y=alt.Y("test_set:N", title=None, sort=None, axis=alt.Axis(labelLimit=260)),
)
labels = alt.Chart(rows).mark_text(align="left", dx=4, fontSize=12).encode(
    x="detected:Q", y=alt.Y("test_set:N", sort=None), text="detected:Q")
show(bars + labels, 200)
st.caption("Same models, tests and 16 injected anomalies as the paper (paper: 7, 16, 16). "
           "Tests from two free LLMs also reach 16/16, in five repeated and five regenerated trials.")

# ------------------------------------------------------------------ 2 pruning
st.subheader("2. How many tests are needed")
order = ["All working tests", "Minimal set"]
prune = scoring.melt(id_vars=["run"], value_vars=["working_tests", "minimal_set"], var_name="set", value_name="tests")
prune["set"] = prune["set"].map({"working_tests": order[0], "minimal_set": order[1]})
show(alt.Chart(prune).mark_bar(stroke=BLACK, strokeWidth=1, size=14).encode(
    y=alt.Y("run:N", title=None, sort=None, axis=alt.Axis(labelLimit=260)),
    yOffset=alt.YOffset("set:N", sort=order),
    x=alt.X("tests:Q", title="Tests"),
    **two_tone("set", order),
    tooltip=["run", "set", "tests"],
), 260)
st.caption(f"Every test is run on {football['mutants'].iloc[0]} (football) and {olist['mutants'].iloc[0]} (Olist) "
           "mutated databases. A greedy set cover keeps the smallest set that still catches every detectable "
           "mutant: about a third of the tests on football, but most of them on Olist, where more tables "
           "and columns give each test something unique to catch.")

# ------------------------------------------------------------------ 3 held-out
st.subheader("3. Does the minimal set catch new kinds of damage?")
order = ["Fixed split", "Each type held out in turn (average)"]
held = pd.DataFrame({
    "run": list(scoring["run"]) * 2,
    "split": [order[0]] * len(scoring) + [order[1]] * len(scoring),
    "pct": list(scoring["heldout_fixed"].map(lambda h: h["kept_pct_of_full"])) +
           list(scoring["leave_one_out_mean_pct_of_full"]),
})
show(alt.Chart(held).mark_bar(stroke=BLACK, strokeWidth=1, size=14).encode(
    y=alt.Y("run:N", title=None, sort=None, axis=alt.Axis(labelLimit=260)),
    yOffset=alt.YOffset("split:N", sort=order),
    x=alt.X("pct:Q", title="Share of the full set's catches kept (%)", scale=alt.Scale(domain=[0, 100])),
    **two_tone("split", order),
    tooltip=["run", "split", "pct"],
), 260)
ops = ", ".join(o.replace("_", " ") for o in scoring["heldout_fixed"].iloc[0]["operators"])
st.caption(f"The minimal set is chosen on some mutation types and measured on types it never saw "
           f"(fixed split: {ops}). Pruning keeps what it is measured on and drops the rest: on Olist the "
           "kept set catches under 3% of what the full set catches on the unseen types.")

choice = st.selectbox("Per mutation type, when that type is held out", list(scoring["run"]))
row = scoring[scoring["run"] == choice].iloc[0]
order = ["All tests", "Minimal set"]
per_op = pd.DataFrame([
    {"type": f["operator"].replace("_", " "), "set": s, "pct": round(100 * c / f["mutants"], 1) if f["mutants"] else 0}
    for f in row["leave_one_out"]
    for s, c in ((order[0], f["caught_by_full_set"]), (order[1], f["caught_by_kept_set"]))
])
show(alt.Chart(per_op).mark_bar(stroke=BLACK, strokeWidth=1, size=10).encode(
    y=alt.Y("type:N", title=None, sort=None),
    yOffset=alt.YOffset("set:N", sort=order),
    x=alt.X("pct:Q", title="Mutants caught (%)", scale=alt.Scale(domain=[0, 100])),
    **two_tone("set", order),
    tooltip=["type", "set", "pct"],
), 380)

# ------------------------------------------------------------------ 4 stability
st.subheader("4. Are the generated tests stable?")
stab = pd.DataFrame(DATA["stability"])
stab = stab[stab["trial"] > 1].assign(pct=lambda d: (100 * d["identical_to_trial_1"] / d["tests"]).round(1))
llms = list(dict.fromkeys(stab["llm"]))
line = alt.Chart(stab).mark_line(color=BLACK, point=alt.OverlayMarkDef(filled=True, fill=BLACK, size=50)).encode(
    x=alt.X("trial:O", title="Fresh generation (trial)", axis=alt.Axis(labelAngle=0)),
    y=alt.Y("pct:Q", title="Tests identical to trial 1 (%)", scale=alt.Scale(domain=[60, 100])),
    strokeDash=alt.StrokeDash("llm:N", sort=llms, scale=alt.Scale(domain=llms, range=[[1, 0], [5, 4]]),
                              legend=None),
    tooltip=["llm", "trial", "tests", "identical_to_trial_1", "pct"],
)
ends = stab[stab["trial"] == stab["trial"].max()]
names = alt.Chart(ends).mark_text(align="left", dx=8, fontSize=12).encode(
    x="trial:O", y="pct:Q", text="llm:N")
show(line + names, 220)
st.caption("Detection on the paper's 16 anomalies stays at 16/16 in every trial, yet each fresh generation "
           "shares only part of its tests with the first one. The small benchmark hides this; the "
           "mutation scores do not.")

st.divider()
st.caption(f"Data: dashboard/results.json (exported {DATA['exported']}). Full numbers in RESULTS.md.")
