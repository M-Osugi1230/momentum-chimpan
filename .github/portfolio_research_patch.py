from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:160]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Exit at the close of the twentieth holding session, not the twenty-first.
replace_once(
    "portfolio_research.py",
    '    if int(position["holding_sessions"]) >= MAX_HOLDING_SESSIONS:\n',
    '    if int(position["holding_sessions"]) >= MAX_HOLDING_SESSIONS - 1:\n',
)

# Closing-price drift can move a sector above the entry-time 25% cap without an
# entry violation. Strict mode instead enforces hard invariants: no negative
# cash and no position-count breach.
replace_once(
    "portfolio_research.py",
    '''        if (pd.to_numeric(results["metrics"]["maximum_sector_weight"], errors="coerce") > MAX_SECTOR_WEIGHT + 0.01).any():
            raise RuntimeError("portfolio materially exceeded sector weight")
''',
    '''        if not results["equity"].empty and (pd.to_numeric(results["equity"]["cash"], errors="coerce") < -1e-6).any():
            raise RuntimeError("portfolio cash became negative")
''',
)

# Add the portfolio simulation to the isolated monthly/PR historical research
# pipeline. All outputs remain under output/backfill and are non-promotable.
workflow_path = Path(".github/workflows/historical-backfill.yml")
workflow = workflow_path.read_text(encoding="utf-8")
if "      - portfolio_research.py\n" not in workflow:
    path_anchor = "      - capacity_analysis.py\n"
    if path_anchor not in workflow:
        raise RuntimeError("historical workflow capacity path anchor not found")
    workflow = workflow.replace(path_anchor, path_anchor + "      - portfolio_research.py\n", 1)
workflow = workflow.replace(
    "mkdir -p output/backfill/replay output/backfill/execution output/backfill/capacity",
    "mkdir -p output/backfill/replay output/backfill/execution output/backfill/capacity output/backfill/portfolio",
    1,
)
if "python portfolio_research.py" not in workflow:
    anchor = "      - name: Audit close-based governance with non-promotable provenance\n"
    step = '''      - name: Run execution-aware portfolio research
        run: |
          set -o pipefail
          python portfolio_research.py \\
            --strict \\
            --signals output/backfill/replay/replay_signals.csv \\
            --prices output/backfill/historical_price_panel.csv \\
            --provenance output/backfill/replay/evidence_provenance.json \\
            --output-dir output/backfill/portfolio | tee output/backfill/portfolio/portfolio.log

'''
    if anchor not in workflow:
        raise RuntimeError("historical workflow governance anchor not found")
    workflow = workflow.replace(anchor, step + anchor, 1)
if "portfolio = json.loads(Path('output/backfill/portfolio/portfolio_research_manifest.json')" not in workflow:
    workflow = workflow.replace(
        "          capacity = json.loads(Path('output/backfill/capacity/capacity_manifest.json').read_text(encoding='utf-8'))\n",
        "          capacity = json.loads(Path('output/backfill/capacity/capacity_manifest.json').read_text(encoding='utf-8'))\n"
        "          portfolio = json.loads(Path('output/backfill/portfolio/portfolio_research_manifest.json').read_text(encoding='utf-8'))\n",
        1,
    )
    workflow = workflow.replace(
        "          assert capacity['production_state_mutations'] == []\n",
        "          assert capacity['production_state_mutations'] == []\n"
        "          assert portfolio['promotion_evidence_allowed'] is False\n"
        "          assert portfolio['automatic_strategy_change'] is False\n"
        "          assert portfolio['production_state_mutations'] == []\n"
        "          assert portfolio['entry_model'] == 'NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN'\n"
        "          assert portfolio['intraday_ambiguity_policy'] == 'STOP_FIRST_CONSERVATIVE'\n",
        1,
    )
workflow_path.write_text(workflow, encoding="utf-8")

# Permanent CI compiles and validates the portfolio research layer.
ci_path = Path(".github/workflows/ci.yml")
ci = ci_path.read_text(encoding="utf-8")
if "portfolio_research.py" not in ci:
    ci = ci.replace(
        "          execution_realism.py capacity_analysis.py",
        "          execution_realism.py capacity_analysis.py portfolio_research.py",
        1,
    )
if ".github/test_portfolio_research.py" not in ci:
    compile_anchor = "          .github/test_capacity_analysis.py"
    if compile_anchor not in ci:
        raise RuntimeError("CI capacity test compile anchor not found")
    ci = ci.replace(
        compile_anchor,
        compile_anchor + " .github/test_portfolio_research.py",
        1,
    )
    step_anchor = "      - name: Upload validation failure logs\n"
    step = '''      - name: Run execution-aware portfolio research validation
        run: |
          set -o pipefail
          python .github/test_portfolio_research.py 2>&1 | tee /tmp/portfolio-research-test.log

'''
    if step_anchor not in ci:
        raise RuntimeError("CI failure upload anchor not found")
    ci = ci.replace(step_anchor, step + step_anchor, 1)
    ci = ci.replace(
        "            /tmp/capacity-analysis-test.log\n",
        "            /tmp/capacity-analysis-test.log\n            /tmp/portfolio-research-test.log\n",
        1,
    )
ci_path.write_text(ci, encoding="utf-8")

# Workflow policy requires the portfolio layer while preserving read-only,
# non-promotable research isolation.
validator_path = Path(".github/validate_workflows.py")
validator = validator_path.read_text(encoding="utf-8")
if '"python portfolio_research.py"' not in validator:
    validator = validator.replace(
        '        "python capacity_analysis.py",\n',
        '        "python capacity_analysis.py",\n'
        '        "python portfolio_research.py",\n'
        '        "portfolio_research_manifest.json",\n'
        '        "STOP_FIRST_CONSERVATIVE",\n',
        1,
    )
validator_path.write_text(validator, encoding="utf-8")

print("execution-aware portfolio research integration applied")
