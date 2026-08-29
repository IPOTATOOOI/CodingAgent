"""独立验证单文件算术修复。"""

from pathlib import Path
import sys


workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace))
from calculator import add

assert add(2, 3) == 5
assert add(-4, 1) == -3
assert add(0, 0) == 0
