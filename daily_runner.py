"""Daily entrypoint for governed display, quality, research focus, site, and mail audit.

The governed screener still runs through ``main.main``. This wrapper augments
human-facing Excel output and persisted ranking rows with research transparency
and non-mutating data-quality metadata. The downstream site consumes the exact
successful artifact, while this wrapper replaces the long mail body with a
concise decision digest. Momentum scores, ranks, thresholds, paper execution,
and strategy fingerprints remain untouched.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

import pandas as pd

import daily_research_focus
import data_quality
import email_delivery
import email_digest
import main
import research_transparency as transparency

_PATCHED = False

# Compatibility anchors retained for independent transparency and focus contract
# tests. The concise digest no longer injects these long sections into email.
HTML_MARKER = "Market Temperature"
PLAIN_MARKER = "【Market Temperature】"


def insert_plain_section(body: str, section_lines: list[str]) -> str:
    section = "\n".join(section_lines).strip()
    if not section or section in body:
        return body
    if PLAIN_MARKER in body:
        return body.replace(PLAIN_MARKER, f"{section}\n\n{PLAIN_MARKER}", 1)
    return f"{body.rstrip()}\n\n{section}\n"


def insert_html_section(body: str, section: str) -> str:
    if not section or section in body:
        return body
    if HTML_MARKER in body:
        return body.replace(HTML_MARKER, f"{section}{HTML_MARKER}", 1)
    return body.replace("</body>", f"{section}</body>", 1)


def legacy_daily_focus_sections(frame: pd.DataFrame) -> tuple[list[str], str]:
    """Expose full focus sections for workbook/site tests, not daily email."""
    return (
        daily_research_focus.plain_section(frame),
        daily_research_focus.html_section(frame),
    )


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


def _with_summary(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    summary: dict[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Replace only the presentation summary while preserving every data frame."""
    if not summary:
        return args, kwargs
    if args:
        return (dict(summary), *args[1:]), kwargs
    prepared = dict(kwargs)
    prepared["summary"] = dict(summary)
    return args, prepared


def install_patches(
    snapshot: dict[str, Any] | None = None,
    main_module: Any = main,
) -> dict[str, Any]:
    global _PATCHED
    if _PATCHED:
        return snapshot or transparency.load_snapshot()

    current = snapshot or transparency.load_snapshot()
    original_excel: Callable[..., Any] = main_module.excel_report
    original_provenance: Callable[[pd.DataFrame], pd.DataFrame] = (
        main_module.attach_strategy_provenance
    )
    config = main_module.load_config()
    minimum_trading_value = float(config["market"]["min_trading_value"])
    display_context: dict[str, Any] = {
        "top100": pd.DataFrame(),
        "action_priority": pd.DataFrame(),
        "daily_focus": pd.DataFrame(),
        "email_summary": {},
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
        enriched = enrich_summary(
            summary,
            current,
            quality_fields,
            focus_fields,
        )
        # The same enriched summary must reach both workbook and email. Mutating
        # this display dictionary does not affect ranking or strategy state.
        summary.clear()
        summary.update(enriched)
        display_context["email_summary"] = dict(enriched)
        result = original_excel(
            path,
            summary,
            *args,
            **kwargs,
        )
        transparency.patch_workbook(path, current)
        data_quality.patch_workbook(path, top100, focused_priority)
        daily_research_focus.patch_workbook(path, focused_priority)
        return result

    def patched_plain(*args: Any, **kwargs: Any) -> str:
        prepared_args, prepared_kwargs = _with_summary(
            args,
            kwargs,
            display_context["email_summary"],
        )
        return email_digest.build_plain(
            *prepared_args,
            **prepared_kwargs,
            daily_focus=display_context["daily_focus"],
            snapshot=current,
        )

    def patched_html(*args: Any, **kwargs: Any) -> str:
        prepared_args, prepared_kwargs = _with_summary(
            args,
            kwargs,
            display_context["email_summary"],
        )
        return email_digest.build_html(
            *prepared_args,
            **prepared_kwargs,
            daily_focus=display_context["daily_focus"],
            snapshot=current,
        )

    def send_decision_email(*args: Any, **kwargs: Any) -> None:
        """Use the original SMTP contract with the decision-first subject/body."""
        main_module.load_dotenv()
        sender = main_module.os.getenv("EMAIL_FROM")
        recipient = main_module.os.getenv("EMAIL_TO")
        password = main_module.os.getenv("EMAIL_APP_PASSWORD")
        if not sender or not recipient or not password:
            main_module.logger.info("Email secrets are not set; skip email")
            return
        summary = args[0] if args else kwargs.get("summary", {})
        msg = main_module.MIMEMultipart("alternative")
        msg["Subject"] = email_digest.subject(
            summary,
            display_context["daily_focus"],
        )
        msg["From"], msg["To"] = sender, recipient
        msg.attach(
            main_module.MIMEText(
                main_module.build_plain_email(*args, **kwargs),
                "plain",
                "utf-8",
            )
        )
        msg.attach(
            main_module.MIMEText(
                main_module.build_html_email(*args, **kwargs),
                "html",
                "utf-8",
            )
        )
        with main_module.smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)

    def patched_send_email(*args: Any, **kwargs: Any) -> Any:
        # Keep receipt classification aligned with the exact summary and subject
        # that are passed to SMTP.
        main_module.load_dotenv()
        prepared_args, prepared_kwargs = _with_summary(
            args,
            kwargs,
            display_context["email_summary"],
        )
        summary = prepared_args[0] if prepared_args else prepared_kwargs.get("summary", {})
        subject_text = email_digest.subject(summary, display_context["daily_focus"])
        return email_delivery.send_with_receipt(
            send_decision_email,
            *prepared_args,
            **prepared_kwargs,
            subject_text=subject_text,
        )

    main_module.attach_strategy_provenance = patched_provenance
    main_module.excel_report = patched_excel
    main_module.build_plain_email = patched_plain
    main_module.build_html_email = patched_html
    main_module.send_email = patched_send_email
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
    main.logger.info(
        "Email delivery receipt: version=%s output=%s inbox_delivery_claimed=false",
        email_delivery.RECEIPT_VERSION,
        email_delivery.DEFAULT_RECEIPT_PATH,
    )
    main.logger.info(
        "Presentation split: email=concise_decision_digest site=rich_static_dashboard site_url=%s",
        email_digest.resolve_site_url(main.load_config()),
    )
    main.main()


if __name__ == "__main__":
    run()
