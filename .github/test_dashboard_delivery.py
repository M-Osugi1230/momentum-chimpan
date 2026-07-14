from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def require(text: str, fragments: list[str]) -> None:
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise AssertionError(f"dashboard delivery is missing controls: {missing}")


def forbid(text: str, fragments: list[str]) -> None:
    found = [fragment for fragment in fragments if fragment in text]
    if found:
        raise AssertionError(f"dashboard delivery contains forbidden controls: {found}")


def main() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "publish-dashboard.yml"
    text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert isinstance(workflow, dict)
    permissions = workflow["permissions"]
    assert permissions["actions"] == "read"
    assert permissions["contents"] == "read"
    assert permissions["pages"] == "write"
    assert permissions["id-token"] == "write"
    require(text, [
        "Daily Momentum Report",
        "source_run_id:",
        "gh api",
        "head_branch",
        "Pull-request source runs are ineligible",
        "actions/download-artifact@v4",
        "run-id: ${{ steps.source.outputs.run_id }}",
        "downloaded-daily-report/output/daily_report.xlsx",
        "python site_builder.py build",
        "python site_experience.py apply",
        "python site_builder.py validate",
        "python site_experience.py validate",
        "output/site/assets/experience.css",
        "output/site/assets/experience.js",
        "actions/configure-pages@v5",
        "actions/upload-pages-artifact@v4",
        "actions/deploy-pages@v4",
        "environment:",
        "name: github-pages",
    ])
    forbid(text, [
        "git push",
        "contents: write",
        "EMAIL_APP_PASSWORD",
        "EMAIL_FROM",
        "EMAIL_TO",
        "data/momentum_daily_ranking.csv \\",
        "data/paper_portfolio.csv \\",
    ])

    daily_runner = (ROOT / "daily_runner.py").read_text(encoding="utf-8")
    require(daily_runner, [
        "import email_digest",
        "email_digest.build_plain",
        "email_digest.build_html",
        "main_module.build_plain_email = patched_plain",
        "main_module.build_html_email = patched_html",
    ])
    forbid(daily_runner, [
        "automatic_score_change = True",
        "automatic_weight_change = True",
        "automatic_strategy_change = True",
    ])

    site_source = (ROOT / "site_builder.py").read_text(encoding="utf-8")
    require(site_source, [
        '"research_only": True',
        '"production_state_mutations": []',
        "downloads/daily_report.xlsx",
        "site_manifest.json",
    ])
    experience_source = (ROOT / "site_experience.py").read_text(encoding="utf-8")
    require(experience_source, [
        "momentum-watchlist-v2",
        "momentum-compare-v2",
        "URLSearchParams",
        "ux-mobile-ranking",
        '"research_only": True',
        '"production_state_mutations": []',
        "reseal_manifest",
        "for forbidden in",
        "forbidden private marker",
    ])
    # The overlay intentionally contains private-marker names as detection rules.
    # Validate that it never contains strategy mutation switches instead of
    # mistaking its secret-leak scanner for leaked secret values.
    forbid(experience_source, [
        "automatic_score_change = True",
        "automatic_weight_change = True",
        "automatic_strategy_change = True",
    ])
    print("dashboard delivery safety validation passed")


if __name__ == "__main__":
    main()
