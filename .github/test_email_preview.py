from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import email_preview


def main() -> None:
    summary = {
        "実行日": "2026-07-13",
        "株価データ日": "2026-07-13",
        "Market Regime": "強気",
        "Market Regime Score": 93,
    }
    subject = "【モメンタムチンパン】2026-07-13 強気93｜調査3件"
    plain = "市場: 強気 93点\n候補: 7453 良品計画\nhttps://example.test/?code=7453#ranking"
    html = '<!doctype html><html lang="ja"><body><h1>強気 93点</h1><a href="https://example.test/?code=7453#ranking">良品計画</a></body></html>'

    with TemporaryDirectory() as directory:
        output = Path(directory) / "preview"
        result = email_preview.write_preview(
            subject=subject,
            plain=plain,
            html=html,
            summary=summary,
            candidate_count=3,
            output_dir=output,
        )
        assert result["validation"]["passed"] is True
        validation = email_preview.validate_preview(output)
        assert validation["passed"] is True
        manifest = validation["manifest"]
        assert manifest["report_date"] == "2026-07-13"
        assert manifest["price_date"] == "2026-07-13"
        assert manifest["market_regime"] == "強気"
        assert manifest["market_regime_score"] == "93"
        assert manifest["candidate_count"] == 3
        assert manifest["contains_recipient_identity"] is False
        assert manifest["contains_sender_identity"] is False
        assert manifest["contains_credentials"] is False
        assert manifest["research_only"] is True
        assert manifest["automatic_strategy_change"] is False
        assert manifest["production_state_mutations"] == []
        assert (output / "subject.txt").read_text(encoding="utf-8").strip() == subject
        assert (output / "plain.txt").read_text(encoding="utf-8") == plain
        assert (output / "email.html").read_text(encoding="utf-8") == html

        serialized = "\n".join(
            (output / name).read_text(encoding="utf-8")
            for name in email_preview.REQUIRED_FILES
        )
        for forbidden in email_preview.FORBIDDEN_MARKERS:
            assert forbidden.lower() not in serialized.lower()

        (output / "plain.txt").write_text(plain + "\ntampered", encoding="utf-8")
        tampered = email_preview.validate_preview(output)
        assert tampered["passed"] is False
        assert "file hash mismatch: plain.txt" in tampered["issues"]
        assert "plain content hash mismatch" in tampered["issues"]

        try:
            email_preview.write_preview(
                subject="invalid\nsubject",
                plain=plain,
                html=html,
                output_dir=output,
            )
            raise AssertionError("multiline subject must fail")
        except ValueError as error:
            assert "one line" in str(error)

        manifest_json = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert "sender" not in manifest_json
        assert "recipient" not in manifest_json
        print("signed privacy-safe email preview validation passed")


if __name__ == "__main__":
    main()
