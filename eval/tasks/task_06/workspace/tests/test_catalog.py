import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from catalog.labels import product_label

assert product_label("  Keyboard ", "KB-10") == "Keyboard [KB-10]"
