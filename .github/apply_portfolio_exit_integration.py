from pathlib import Path


workflow_path = Path('.github/workflows/historical-backfill.yml')
workflow = workflow_path.read_text(encoding='utf-8')
if '      - portfolio_exit_lab.py\n' not in workflow:
    workflow = workflow.replace(
        '      - portfolio_filter_lab.py\n',
        '      - portfolio_filter_lab.py\n      - portfolio_exit_lab.py\n',
        1,
    )
workflow = workflow.replace(
    'mkdir -p output/backfill/replay output/backfill/execution output/backfill/portfolio output/backfill/portfolio_filters',
    'mkdir -p output/backfill/replay output/backfill/execution output/backfill/portfolio output/backfill/portfolio_filters output/backfill/portfolio_exits',
    1,
)
if 'python portfolio_exit_lab.py' not in workflow:
    anchor = '      - name: Audit close-based governance with non-promotable provenance\n'
    step = '''      - name: Compare governed portfolio exit policies
        run: |
          set -o pipefail
          python portfolio_exit_lab.py \
            --strict \
            --signals output/backfill/replay/replay_signals.csv \
            --history output/backfill/historical_ranking.csv \
            --prices output/backfill/historical_price_panel.csv \
            --provenance output/backfill/replay/evidence_provenance.json \
            --output-dir output/backfill/portfolio_exits | tee output/backfill/portfolio_exits/exit-lab.log

'''
    if anchor not in workflow:
        raise RuntimeError('historical exit lab step anchor not found')
    workflow = workflow.replace(anchor, step + anchor, 1)
if 'portfolio_exit = json.loads' not in workflow:
    workflow = workflow.replace(
        "          portfolio_filter = json.loads(Path('output/backfill/portfolio_filters/portfolio_filter_manifest.json').read_text(encoding='utf-8'))\n",
        "          portfolio_filter = json.loads(Path('output/backfill/portfolio_filters/portfolio_filter_manifest.json').read_text(encoding='utf-8'))\n"
        "          portfolio_exit = json.loads(Path('output/backfill/portfolio_exits/portfolio_exit_manifest.json').read_text(encoding='utf-8'))\n",
        1,
    )
    workflow = workflow.replace(
        "          assert portfolio_filter['entry_model'] == 'NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN'\n",
        "          assert portfolio_filter['entry_model'] == 'NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN'\n"
        "          assert portfolio_exit['promotion_evidence_allowed'] is False\n"
        "          assert portfolio_exit['automatic_strategy_change'] is False\n"
        "          assert portfolio_exit['automatic_exit_activation'] is False\n"
        "          assert portfolio_exit['production_state_mutations'] == []\n"
        "          assert portfolio_exit['entry_model'] == 'NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN'\n",
        1,
    )
workflow_path.write_text(workflow, encoding='utf-8')

ci_path = Path('.github/workflows/ci.yml')
ci = ci_path.read_text(encoding='utf-8')
if 'portfolio_exit_lab.py' not in ci:
    ci = ci.replace(
        '          capacity_analysis.py portfolio_research.py portfolio_filter_lab.py\n',
        '          capacity_analysis.py portfolio_research.py portfolio_filter_lab.py portfolio_exit_lab.py\n',
        1,
    )
if '.github/test_portfolio_exit_lab.py' not in ci:
    ci = ci.replace(
        '          .github/test_portfolio_research.py .github/test_portfolio_filter_lab.py\n',
        '          .github/test_portfolio_research.py .github/test_portfolio_filter_lab.py\n'
        '          .github/test_portfolio_exit_lab.py\n',
        1,
    )
    anchor = '      - name: Run operational controls validation\n'
    step = '''      - name: Run portfolio exit lab validation
        run: |
          set -o pipefail
          python .github/test_portfolio_exit_lab.py 2>&1 | tee /tmp/portfolio-exit-lab-test.log

'''
    if anchor not in ci:
        raise RuntimeError('CI exit lab step anchor not found')
    ci = ci.replace(anchor, step + anchor, 1)
    ci = ci.replace(
        '            /tmp/portfolio-filter-lab-test.log\n',
        '            /tmp/portfolio-filter-lab-test.log\n            /tmp/portfolio-exit-lab-test.log\n',
        1,
    )
ci_path.write_text(ci, encoding='utf-8')

validator_path = Path('.github/validate_workflows.py')
validator = validator_path.read_text(encoding='utf-8')
if '"python portfolio_exit_lab.py"' not in validator:
    validator = validator.replace(
        '        "automatic_filter_activation",\n',
        '        "automatic_filter_activation",\n'
        '        "python portfolio_exit_lab.py",\n'
        '        "portfolio_exit_manifest.json",\n'
        '        "automatic_exit_activation",\n',
        1,
    )
validator_path.write_text(validator, encoding='utf-8')

print('portfolio exit lab permanently integrated')
