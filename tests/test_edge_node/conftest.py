"""
Puts gateway/ on sys.path so `import edge_node_improved` works without
installing the project as a package. Must run before any test module's own
`import edge_node_improved` - pytest always imports conftest.py first, so
this is the right place for it.
"""

import sys
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[2] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))
