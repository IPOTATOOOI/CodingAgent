"""独立验证需要搜索定位的嵌套实现。"""

from pathlib import Path
import sys


workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace / "src"))
from catalog.labels import product_label

assert product_label("  Keyboard ", "KB-10") == "Keyboard [KB-10]"
assert product_label("Mouse", "MS-2") == "Mouse [MS-2]"
