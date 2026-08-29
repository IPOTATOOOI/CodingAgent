"""独立验证字符串处理修复。"""

from pathlib import Path
import sys


workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace))
from text_utils import slugify

assert slugify("Hello World") == "hello-world"
assert slugify("Already-Slug") == "already-slug"
assert slugify("  three words here ") == "three-words-here"
