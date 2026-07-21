"""Public facade for Detailed OOS Evidence v2.

The implementation is split by concern to keep review and CI memory bounded.
"""
from detailed_oos_shared import *
from detailed_oos_metrics import *
from detailed_oos_path import *
from detailed_oos_experiments import *
from detailed_oos_cli import main_cli

if __name__ == "__main__":
    raise SystemExit(main_cli())
