"""
Puts src/Venko_Green on sys.path so `import edge_node_improved` works
without installing the project as a package. Must run before any test
module's own `import edge_node_improved` - pytest always imports conftest.py
first, so this is the right place for it.
"""

import sys
from pathlib import Path

VENKO_GREEN_DIR = Path(__file__).resolve().parents[2] / "src" / "Venko_Green"
sys.path.insert(0, str(VENKO_GREEN_DIR))
