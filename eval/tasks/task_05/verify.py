"""独立验证新模块创建和现有代码集成。"""

from pathlib import Path
import sys


workspace = Path(sys.argv[1]).resolve()
assert (workspace / "validators.py").is_file()
sys.path.insert(0, str(workspace))
from profile import create_profile
from validators import is_valid_username

assert is_valid_username("User_123")
assert not is_valid_username("ab")
assert not is_valid_username("bad-name")
assert create_profile(" User_123 ") == {"username": "User_123"}
for invalid in ("ab", "bad-name"):
    try:
        create_profile(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(f"应拒绝无效用户名: {invalid}")
