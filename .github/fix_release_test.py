from pathlib import Path

path = Path(".github/test_release_readiness.py")
text = path.read_text(encoding="utf-8")
old = 'paper_no_trades.loc[:, "win_rate"] = None'
new = 'paper_no_trades.loc[:, "win_rate"] = float("nan")'
if text.count(old) != 1:
    raise RuntimeError("Could not find pandas missing-value test anchor")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Prepared pandas-compatible release readiness test")
