"""Run the live-session eligibility ledger with recovery-aware readiness."""
from __future__ import annotations

import live_session_eligibility as eligibility
import live_session_readiness_with_recovery as readiness

# ``live_session_eligibility.build_record`` resolves this module global at run
# time. Replacing it keeps the existing ledger and signature contract while
# requiring the recovery-aware readiness payload for every new source run.
eligibility.readiness = readiness


if __name__ == "__main__":
    raise SystemExit(eligibility.main_cli())
