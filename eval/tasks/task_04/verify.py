"""独立验证跨文件收据计算。"""

from pathlib import Path
import sys


workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace))
from discount import apply_discount
from receipt import receipt_total

assert apply_discount(100, 0.25) == 75
assert receipt_total([50, 30, 20], 0.1) == 90
assert receipt_total([7, 3], 0.0) == 10
