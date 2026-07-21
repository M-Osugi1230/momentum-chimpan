"""Safe extended-horizon entrypoint for the historical OOS analyzer."""
from __future__ import annotations

import analyze_historical_oos as analysis
import run_historical_oos_analysis as safe

DETAILED_HORIZONS = (1, 3, 5, 10, 20, 40, 60)


def main_cli() -> int:
    analysis.DEFAULT_HORIZONS = DETAILED_HORIZONS
    safe.analysis.DEFAULT_HORIZONS = DETAILED_HORIZONS
    return safe.main_cli()


if __name__ == "__main__":
    raise SystemExit(main_cli())
