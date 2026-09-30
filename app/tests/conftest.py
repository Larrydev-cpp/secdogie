import sys
from pathlib import Path

# The end-to-end test drives the real node with the node suite's stand-ins
# (a scripted model, a fake desktop): node/tests/fakes.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "node" / "tests"))
