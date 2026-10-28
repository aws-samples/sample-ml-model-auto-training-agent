from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
LAMBDA_ROOT = ROOT / "lambda"

for path in (ROOT, LAMBDA_ROOT):
    as_str = str(path)
    if as_str not in sys.path:
        sys.path.insert(0, as_str)
