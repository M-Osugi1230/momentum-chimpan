"""Daily entrypoint for governed display, quality, and research focus.

The governed screener still runs through ``main.main``. This wrapper augments
human-facing Excel/email outputs and persisted ranking rows with research
transparency and non-mutating data-quality metadata. It also converts the
existing action-priority table into a concise daily research plan after paper
execution has already completed. Momentum scores, ranks, thresholds, paper
execution, and strategy fingerprints remain untouched.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

import pandas as pd

import daily_research_focus
import data_quality
import main
import research_transparency as transparency

PLAIN_MARKER = "【Market Temperature】"
HTML_MARKER = (
    '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;'
    'padding:16px;margin-top:14px"><b>Market Temperature</b>'
)
_PATCHED = False


def insert_plain_section(body: str, section: list[str]) -> str:
    block = "\n".join(section)
    if PLAIN_MARKER in body:
        return body.replace(PLAIN_MARKER, f"{block}\n{PLAIN_MARKER}", 1)
    return f"{block}\n{body}"


def insert_html_section(body: str, section: str) -> str:
    if HTML_MARKER in body:
        return body.replace(HTML_MARKER, f"{section}{HTML_MARKER}", 1)
    closing = "</div></body></html>"
    if closing in body:
        return body.replace(closing, f"{section}{closing}", 1)
    return f"{section}{body}"


def enrich_summary(
    summary: dict[str, Any],
    snapshot: dict[str, Any],
    quality_fields: dict[str, Any] | None = None,
    focus_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(summary)
    result.update(transparency.summary_fields(snapshot))
    if quality_fields:
        result.update(quality_fields)
    if focus_fields:
        result.update(focus_fields)
    return result


def argument_frame(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    index: int,
    name: str,
) -> pd.DataFrame:
    if len(args) > index and isinstance(args[index], pd.DataFrame):
        return args[index]
    value = kwargs.get(name)
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def install_patches(
    snapshot: dict[str, Any] | None = None,
    main_module: Any = main,
) -> dict[str, Any]:
    global _PATCHED
    if _PATCHED:
        return snapshot or transparency.load_snapshot()

    current = snapshot or transparency.load_snapshot()
    original_excel: Callable[..., Any] = main_module.excel_report
    original_plain: Callable[..., str] = main_module.build_plain_email
    original_html: Callable[..., str] = main_module.build_html_email
    original_provenance: Callable[[pd.DataFrame], pd.DataFrame] = (
        main_module.attach_strategy_provenance
    )
    config = main_module.load_config()
    minimum_trading_value = float(config["market"]["min_trading_value"])
    display_context: dict[str, pd.DataFrame] = {
        "top100": pd.DataFrame(),
        "action_priority": pd.DataFrame(),
        "daily_focus": pd.DataFrame(),
    }

    @wraps(original_provenance)
    def patched_provenance(frame: pd.DataFrame) -> pd.DataFrame:
        stamped = original_provenance(frame)
        return data_quality.attach_quality(
            stamped,
            minimum_trading_value=minimum_trading_value,
        )

    @wraps(original_excel)
    def patched_excel(
        path: str,
        summary: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        top100 = argument_frame(args, kwargs, 0, "top100")
        action_priority = argument_frame(args, kwargs, 29, "action_priority")
        gated_priority = data_quality.apply_priority_gate(action_priority, top100)
        focused_priority = daily_research_focus.attach_daily_focus(
            gated_priority,
            top100,
        )
        if not action_priority.empty or not focused_priority.empty:
            data_quality.replace_frame_in_place(action_priority, focused_priority)
        display_context["top100"] = top100.copy()
        display_context["action_priority"] = focused_priority.copy()
        display_context["daily_focus"] = focused_priority.copy()
        quality_fields = data_quality.summary_fields(top100, focused_priority)
        focus_fields = daily_research_focus.summary_fields(focused_priority)
        result = original_excel(
            path,
            enrich_summary(
                summary,
                current,
                quality_fields,
                focus_fields,
            ),
            *args,
            **kwargs,
        )
        transparency.patch_workbook(path, current)
        data_quality.patch_workbook(path, top100, focused_priority)
        daily_research_focus.patch_workbook(path, focused_priority)
        return result

    @wraps(original_plain)
    def patched_plain(*args: Any, **kwargs: Any) -> str:
        body = original_plain(*args, **kwargs)
        body = insert_plain_section(
            body,
            daily_research_focus.plain_section(display_context["daily_focus"]),
        )
        body = insert_plain_section(
            body,
            data_quality.plain_section(
                display_context["top100"],
                display_context["action_priority"],
            ),
        )
        return insert_plain_section(body, transparency.plain_section(current))

    @wraps(original_html)
    def patched_html(*args: Any, **kwargs: Any) -> str:
        body = original_html(*args, **kwargs)
        body = insert_html_section(
            body,
            daily_research_focus.html_section(display_context["daily_focus"]),
        )
        body = insert_html_section(
            body,
            data_quality.html_section(
                display_context["top100"],
                display_context["action_priority"],
            ),
        )
        return insert_html_section(body, transparency.html_section(current))

    main_module.attach_strategy_provenance = patched_provenance
    main_module.excel_report = patched_excel
    main_module.build_plain_email = patched_plain
    main_module.build_html_email = patched_html
    _PATCHED = True
    return current


def run() -> None:
    snapshot = install_patches()
    main.logger.info(
        "Research transparency: health=%s decision=%s forward=%s weight=%s",
        snapshot.get("catalog_health"),
        snapshot.get("decision"),
        snapshot.get("governing_study_status"),
        snapshot.get("production_weight_points"),
    )
    main.logger.info(
        "Data quality: policy=%s minimum_trading_value=%s score_and_rank_mutation=disabled",
        data_quality.load_policy()["policy"]["id"],
        main.load_config()["market"]["min_trading_value"],
    )
    main.logger.info(
        "Daily research focus: policy=%s A_cap=%s action_list_cap=%s paper_execution_mutation=disabled",
        daily_research_focus.load_policy()["policy"]["id"],
        daily_research_focus.load_policy()["limits"]["maximum_A_candidates"],
        daily_research_focus.load_policy()["limits"]["maximum_daily_action_list"],
    )
    main.main()


if __name__ == "__main__":
    run()
