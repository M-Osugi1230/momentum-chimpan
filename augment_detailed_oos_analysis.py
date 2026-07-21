"""Safe extended-horizon entrypoint for matched-count diagnostics."""
from __future__ import annotations

import augment_historical_oos_analysis as augment

DETAILED_HORIZONS = (1, 3, 5, 10, 20, 40, 60)


def main() -> None:
    augment.HORIZONS = DETAILED_HORIZONS
    augment.main()


if __name__ == "__main__":
    main()
