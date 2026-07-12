"""Daily entrypoint that adds research transparency without changing strategy code.

The governed screener still runs through ``main.main``. This wrapper only augments
human-facing Excel and email outputs with the canonical evidence status. Rankings,
scores, thresholds, state persistence, and strategy fingerprints remain untouched.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

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


def enrich_summary(summary: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    result = dict(summary)
    result.update(transparency.summary_fields(snapshot))
    return result


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

    @wraps(original_excel)
    def patched_excel(path: str, summary: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        result = original_excel(
            path,
            enrich_summary(summary, current),
            *args,
            **kwargs,
        )
        transparency.patch_workbook(path, current)
        return result

    @wraps(original_plain)
    def patched_plain(*args: Any, **kwargs: Any) -> str:
        body = original_plain(*args, **kwargs)
        return insert_plain_section(body, transparency.plain_section(current))

    @wraps(original_html)
    def patched_html(*args: Any, **kwargs: Any) -> str:
        body = original_html(*args, **kwargs)
        return insert_html_section(body, transparency.html_section(current))

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
    main.main()


if __name__ == "__main__":
    run()
