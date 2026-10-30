"""🔬 Eval Observatory — the 4-panel evaluation harness tab.

Loads pre-cached JD scenarios, runs the three automated evaluators in parallel,
lets a reviewer add a human verdict, and surfaces where evaluators agree and
disagree — including Cohen's Kappa between the LLM judge and the human reviewer.

Rendered inside tab3 of app.py via render_eval_observatory().
"""

import glob
import json
import os
from datetime import datetime, timezone

import anthropic
import streamlit as st

from agents.eval_orchestrator import run_eval, run_eval_live
from agents.llm_client import build_client
from models.eval_context import EvalScenario, HumanEvalResult


def _get_client():
    try:
        return build_client()
    except RuntimeError as e:
        st.error(f"Azure OpenAI not configured — cannot run the live pipeline. {e}")
        st.stop()

_SCENARIO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "data", "eval_scenarios")

_VERDICT_EMOJI = {"pass": "✅", "warn": "⚠️", "fail": "❌"}


# ─────────────────────────────────────────────────────────── data loading
@st.cache_data(show_spinner=False)
def _load_scenarios() -> list[EvalScenario]:
    scenarios = []
    for path in sorted(glob.glob(os.path.join(_SCENARIO_DIR, "scenario_*.json"))):
        with open(path) as f:
            scenarios.append(json.load(f))
    # stable demo order: weak, average, strong
    order = {"weak": 0, "average": 1, "strong": 2}
    scenarios.sort(key=lambda s: order.get(s["scenario_id"], 99))
    return scenarios


# ─────────────────────────────────────────────────────────── kappa
def compute_kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's Kappa for two raters over three categories (pass/warn/fail).

    (p_o - p_e) / (1 - p_e). Returns 0.0 for fewer than 2 pairs or when chance
    agreement is total (p_e == 1.0).
    """
    n = len(pairs)
    if n < 2:
        return 0.0
    cats = ("pass", "warn", "fail")
    p_o = sum(1 for a, b in pairs if a == b) / n
    p_e = 0.0
    for c in cats:
        p_a = sum(1 for a, _ in pairs if a == c) / n
        p_b = sum(1 for _, b in pairs if b == c) / n
        p_e += p_a * p_b
    if p_e >= 1.0:
        return 0.0
    return (p_o - p_e) / (1 - p_e)


def _kappa_label(k: float) -> tuple[str, str]:
    """Return (qualitative label, streamlit color name)."""
    if k >= 0.8:
        return "Excellent", "green"
    if k >= 0.6:
        return "Good", "green"
    if k >= 0.4:
        return "Moderate", "orange"
    if k > 0.0:
        return "Poor — evaluator needs recalibration", "red"
    return "None — no better than chance", "red"


def _kappa_is_degenerate(pairs: list[tuple[str, str]]) -> bool:
    """True when every label (both raters) fell in a single category.

    In that case chance agreement is already 100%, kappa is mathematically
    undefined, and compute_kappa floors it to 0.0. This is a *no variety* signal,
    NOT a miscalibrated judge — the two must not read the same to the user.
    """
    cats_used = {v for pair in pairs for v in pair}
    return len(cats_used) < 2


def _kappa_explanation(k: float, agree: int, n: int) -> str:
    """Plain-English 'why this verdict' for a genuine (non-degenerate) kappa."""
    pct = (agree / n * 100) if n else 0.0
    base = (f"Judge and human gave the same verdict on {agree} of {n} labeled "
            f"samples ({pct:.0f}%); κ corrects that for the agreement expected "
            f"by chance.")
    if k >= 0.8:
        why = ("Agreement is near-perfect and well above chance — the judge "
               "tracks human judgment and can be trusted to run unattended.")
    elif k >= 0.6:
        why = ("Agreement is substantial and clearly above chance — the judge "
               "is reliable on this task; just spot-check the disagreements.")
    elif k >= 0.4:
        why = ("Agreement beats chance but there is real divergence — review "
               "each judge-vs-human disagreement before trusting it at scale.")
    elif k > 0.0:
        why = ("Agreement only barely exceeds chance — the judge is weakly "
               "aligned; retune its verdict thresholds in the prompt.")
    elif k == 0.0:
        why = ("Agreement is no better than a coin flip — the judge's verdicts "
               "are not tracking yours; retune its verdict thresholds.")
    else:
        why = ("Agreement is worse than chance — judge and human systematically "
               "disagree; the judge needs recalibration.")
    return f"{base} {why}"


# ─────────────────────────────────────────────────────────── session state
def _init_state() -> None:
    defaults = {
        "eval_result": None,
        "eval_scenario_id": None,
        "kappa_data": [],                  # list[tuple[str, str]]
        "human_labels": {},                # dict[sid -> HumanEvalResult]
        "llm_verdict_at_label": {},        # dict[sid -> str]
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _rebuild_kappa_data() -> None:
    """Derive kappa_data from labeled scenarios (deduped by scenario id)."""
    labels = st.session_state["human_labels"]
    llm = st.session_state["llm_verdict_at_label"]
    st.session_state["kappa_data"] = [
        (llm[sid], labels[sid]["verdict"]) for sid in labels if sid in llm
    ]


# ─────────────────────────────────────────────────────────── panels
def _verdict_badge(container, verdict: str, headline: str) -> None:
    text = f"{_VERDICT_EMOJI.get(verdict, '•')} **{verdict.upper()}** — {headline}"
    if verdict == "pass":
        container.success(text)
    elif verdict == "warn":
        container.warning(text)
    else:
        container.error(text)


def _duration(container, ms: float) -> None:
    container.caption(f":gray[Duration: {ms:.0f} ms]")


def _render_code_panel(c, res: dict) -> None:
    c.markdown("**1. Code / rule-based**")
    _verdict_badge(c, res["verdict"], f"{res['checks_passed']}/{res['checks_total']} checks")
    if res["failures"]:
        with c.expander(f"{len(res['failures'])} issue(s)"):
            for f in res["failures"]:
                st.markdown(f"- {f}")
    else:
        c.markdown(":green[All 7 structural + content checks passed]")
    _duration(c, res["duration_ms"])


def _render_judge_panel(c, res: dict) -> None:
    c.markdown("**2. LLM-as-judge** :gray[(Sonnet)]")
    supported = sum(1 for v in res["dimension_support"].values() if v)
    _verdict_badge(c, res["verdict"], f"{supported}/6 dimensions supported")
    if res["unsupported_scores"]:
        c.markdown("**Unsupported scores:**")
        for d in res["unsupported_scores"]:
            c.markdown(f"- {d}")
    c.markdown(f"**Vagueness flags:** {len(res['vagueness_flags'])}")
    if res["reasoning"]:
        with c.expander("Judge reasoning"):
            st.write(res["reasoning"])
    _duration(c, res["duration_ms"])


def _render_reference_panel(c, res: dict) -> None:
    c.markdown("**3. Reference-based**")
    _verdict_badge(c, res["verdict"], f"avg delta {res['score_delta_avg']:.1f}")
    if res["score_deltas"]:
        worst_dim = max(res["score_deltas"], key=res["score_deltas"].get)
        c.markdown(f"**Worst:** {worst_dim} — Δ{res['score_deltas'][worst_dim]:.1f}")
    c.markdown(f"**Text similarity:** {res['text_similarity']:.2f}")
    _duration(c, res["duration_ms"])


def _render_human_panel(c, scenario_id: str, llm_verdict: str) -> None:
    c.markdown("**4. Human eval**")
    existing = st.session_state.human_labels.get(scenario_id)
    if existing:
        _verdict_badge(c, existing["verdict"], "labeled")
        c.caption(f":gray[Labeled at {existing['labeled_at']}]")
    with c.form(f"human_eval_form_{scenario_id}"):
        options = ["pass", "warn", "fail"]
        default_idx = options.index(existing["verdict"]) if existing else 0
        verdict = st.radio("Your verdict", options, index=default_idx, horizontal=True)
        notes = st.text_area("Notes", value=existing["notes"] if existing else "",
                             placeholder="What did you see?")
        submitted = st.form_submit_button("Submit label", use_container_width=True)
    if submitted:
        st.session_state.human_labels[scenario_id] = HumanEvalResult(
            verdict=verdict,
            notes=notes,
            labeled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        st.session_state["llm_verdict_at_label"][scenario_id] = llm_verdict
        _rebuild_kappa_data()
        st.rerun()


# ─────────────────────────────────────────────────────────── agreement summary
def _render_agreement(result: dict) -> None:
    st.subheader("Evaluator agreement")
    am = result["agreement_matrix"]
    human = st.session_state.human_labels.get(result["scenario_id"])
    human_verdict = human["verdict"] if human else None

    cols = st.columns(4)
    rows = [("Code", am["code"]), ("LLM Judge", am["llm_judge"]),
            ("Reference", am["reference"]),
            ("Human", human_verdict if human_verdict else "—")]
    for col, (name, v) in zip(cols, rows):
        emoji = _VERDICT_EMOJI.get(v, "—")
        col.metric(name, f"{emoji} {v}" if v != "—" else "—")

    st.markdown(
        f"**Disagreement count:** {result['disagreement_count']} of 3 automated "
        f"evaluators diverge from the majority verdict."
    )

    pairs = st.session_state.kappa_data
    if len(pairs) < 2:
        st.caption(
            ":gray[Cohen's Kappa appears after 2+ scenarios are labeled by a human this session.]"
        )
    elif _kappa_is_degenerate(pairs):
        only = next(iter({v for pair in pairs for v in pair}))
        st.markdown(
            "**Cohen's Kappa (LLM judge vs human, this session):** "
            ":gray[not measurable yet]"
        )
        st.caption(
            f":gray[**Why:** all {len(pairs)} labeled samples share the same "
            f"verdict (“{only}”), so there is no variety to measure agreement "
            f"against — chance agreement is already 100%, which makes κ undefined "
            f"(it floors to 0.00). This is a *no-variety* signal, **not** a "
            f"miscalibrated judge. Label scenarios with **differing** verdicts — "
            f"e.g. Weak (fail), Average (warn), Strong (pass) — to get a "
            f"meaningful κ.]"
        )
    else:
        k = compute_kappa(pairs)
        agree = sum(1 for a, b in pairs if a == b)
        label, color = _kappa_label(k)
        st.markdown(
            f"**Cohen's Kappa (LLM judge vs human, this session):** "
            f":{color}[{k:.2f} — {label}]"
        )
        st.caption(f":gray[**Why:** {_kappa_explanation(k, agree, len(pairs))}]")
        st.caption(f":gray[Based on {len(pairs)} labeled samples this session]")


# ─────────────────────────────────────────────────────────── main render
def render_eval_observatory() -> None:
    _init_state()

    st.markdown("### 🔬 Eval Observatory")
    st.caption(
        "Puts the pipeline's own advisor reports under a 4-panel evaluation harness. "
        "The app tells you if your JD is good — this tells you whether you can trust the "
        "pipeline's judgment."
    )

    scenarios = _load_scenarios()
    if not scenarios:
        st.error("No scenarios found in data/eval_scenarios/.")
        return
    by_label = {s["label"]: s for s in scenarios}

    # ── Section A — scenario selector ────────────────────────────────
    with st.container(border=True):
        col_sel, col_run = st.columns([3, 1])
        with col_sel:
            label = st.selectbox("Select scenario", list(by_label.keys()))
            mode = st.radio(
                "Evaluation source",
                ["Use cached (instant)", "Run live pipeline (~1–2 min)"],
                horizontal=True,
            )
        with col_run:
            st.markdown("<div style='height:1.8em'></div>", unsafe_allow_html=True)
            run = st.button("Run Eval ▶", type="primary", use_container_width=True)

    scenario = by_label[label]
    live = mode.startswith("Run live")
    if live:
        st.caption(
            ":gray[Live mode runs the real pipeline (parser → 6 specialists → synthesizer → "
            "Opus advisor) on this scenario's JD, then evaluates the fresh report against the "
            "gold reference. Guardrails are not applied to eval runs.]"
        )

    if run:
        try:
            if live:
                with st.spinner("Running the live JDQI pipeline + 3 evaluators… "
                                "(Opus advisor is slow — up to ~2 min)"):
                    st.session_state.eval_result = run_eval_live(scenario, _get_client())
            else:
                with st.spinner("Running 3 evaluators in parallel…"):
                    st.session_state.eval_result = run_eval(scenario)
            st.session_state.eval_scenario_id = scenario["scenario_id"]
        except Exception as e:  # noqa: BLE001 — surface pipeline failures in the UI
            st.error(f"Live pipeline failed — {e}")
            return

    result = st.session_state.eval_result
    if result is None or st.session_state.eval_scenario_id != scenario["scenario_id"]:
        st.info("Select a scenario and click **Run Eval** to evaluate the cached advisor report.")
        with st.expander("Preview the JD text for this scenario"):
            st.text(scenario["jd_text"])
        return

    # ── Section B — four evaluator panels (2×2) ──────────────────────
    st.divider()
    top_left, top_right = st.columns(2)
    _render_code_panel(top_left.container(border=True), result["code_check"])
    _render_judge_panel(top_right.container(border=True), result["llm_judge"])

    bot_left, bot_right = st.columns(2)
    _render_reference_panel(bot_left.container(border=True), result["reference_score"])
    _render_human_panel(bot_right.container(border=True), result["scenario_id"],
                        result["llm_judge"]["verdict"])

    # ── Section C — agreement summary ────────────────────────────────
    st.divider()
    _render_agreement(result)
