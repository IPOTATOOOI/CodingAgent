"""独立验证缺失函数实现。"""

import importlib.util
from pathlib import Path
import sys


workspace = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location(
    "task_statistics", workspace / "statistics.py"
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

assert module.mean([1, 2, 3, 4]) == 2.5
assert module.mean([-3, 3]) == 0
try:
    module.mean([])
except ValueError:
    pass
else:
    raise AssertionError("mean([]) 应拒绝空输入")
