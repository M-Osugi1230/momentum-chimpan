from pathlib import Path


# Historical research workflow integration.
workflow_path = Path(".github/workflows/historical-backfill.yml")
workflow = workflow_path.read_text(encoding="utf-8")
if "      - portfolio_filter_lab.py\n" not in workflow:
    workflow = workflow.replace(
        "      - portfolio_research.py\n",
        "      - portfolio_research.py\n      - portfolio_filter_lab.py\n",
        1,
    )
workflow = workflow.replace(
    "mkdir -p output/backfill/replay output/backfill/execution output/backfill/portfolio",
    "mkdir -p output/backfill/replay output/backfill/execution output/backfill/portfolio output/backfill/portfolio_filters",
    1,
)
if "python portfolio_filter_lab.py" not in workflow:
    anchor = "      - name: Audit close-based governance with non-promotable provenance\n"
    step = '''      - name: Compare governed portfolio entry filters
        run: |
          set -o pipefail
          python portfolio_filter_lab.py \
            --strict \
            --signals output/backfill/replay/replay_signals.csv \
            --history output/backfill/historical_ranking.csv \
            --prices output/backfill/historical_price_panel.csv \
            --provenance output/backfill/replay/evidence_provenance.json \
            --output-dir output/backfill/portfolio_filters | tee output/backfill/portfolio_filters/filter-lab.log

'''
    if anchor not in workflow:
        raise RuntimeError("historical filter lab step anchor not found")
    workflow = workflow.replace(anchor, step + anchor, 1)
if "portfolio_filter = json.loads" not in workflow:
    workflow = workflow.replace(
        "          portfolio = json.loads(Path('output/backfill/portfolio/portfolio_research_manifest.json').read_text(encoding='utf-8'))\n",
        "          portfolio = json.loads(Path('output/backfill/portfolio/portfolio_research_manifest.json').read_text(encoding='utf-8'))\n"
        "          portfolio_filter = json.loads(Path('output/backfill/portfolio_filters/portfolio_filter_manifest.json').read_text(encoding='utf-8'))\n",
        1,
    )
    workflow = workflow.replace(
        "          assert portfolio['intraday_ambiguity_policy'] == 'STOP_FIRST_CONSERVATIVE'\n",
        "          assert portfolio['intraday_ambiguity_policy'] == 'STOP_FIRST_CONSERVATIVE'\n"
        "          assert portfolio_filter['promotion_evidence_allowed'] is False\n"
        "          assert portfolio_filter['automatic_strategy_change'] is False\n"
        "          assert portfolio_filter['automatic_filter_activation'] is False\n"
        "          assert portfolio_filter['production_state_mutations'] == []\n"
        "          assert portfolio_filter['entry_model'] == 'NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN'\n",
        1,
    )
workflow_path.write_text(workflow, encoding="utf-8")


# Permanent CI integration.
ci_path = Path(".github/workflows/ci.yml")
ci = ci_path.read_text(encoding="utf-8")
if "portfolio_filter_lab.py" not in ci:
    ci = ci.replace(
        "          capacity_analysis.py portfolio_research.py .github/validate_workflows.py",
        "          capacity_analysis.py portfolio_research.py portfolio_filter_lab.py .github/validate_workflows.py",
        1,
    )
if ".github/test_portfolio_filter_lab.py" not in ci:
    ci = ci.replace(
        "          .github/test_portfolio_research.py\n",
        "          .github/test_portfolio_research.py .github/test_portfolio_filter_lab.py\n",
        1,
    )
    anchor = "      - name: Run operational controls validation\n"
    step = '''      - name: Run portfolio filter lab validation
        run: |
          set -o pipefail
          python .github/test_portfolio_filter_lab.py 2>&1 | tee /tmp/portfolio-filter-lab-test.log

'''
    if anchor not in ci:
        raise RuntimeError("permanent CI filter lab step anchor not found")
    ci = ci.replace(anchor, step + anchor, 1)
    ci = ci.replace(
        "            /tmp/portfolio-research-test.log\n",
        "            /tmp/portfolio-research-test.log\n            /tmp/portfolio-filter-lab-test.log\n",
        1,
    )
ci_path.write_text(ci, encoding="utf-8")


# Workflow policy validation.
validator_path = Path(".github/validate_workflows.py")
validator = validator_path.read_text(encoding="utf-8")
if '"python portfolio_filter_lab.py"' not in validator:
    validator = validator.replace(
        '        "python portfolio_research.py",\n',
        '        "python portfolio_research.py",\n'
        '        "python portfolio_filter_lab.py",\n'
        '        "portfolio_filter_manifest.json",\n'
        '        "automatic_filter_activation",\n',
        1,
    )
validator_path.write_text(validator, encoding="utf-8")

print("portfolio filter lab permanently integrated")
