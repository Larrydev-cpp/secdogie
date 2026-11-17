"""The decision model does not run on text detection.

No module on the path from what the person says to what the companion does may match words: no regular
expressions, and none of the old word-table modules. Meaning comes from the cortex, which learned it.
(The operator's own signed phrase rules, ``rules.py``, are the person's rules, not the decision model's, and
stay where they are.)
"""
from __future__ import annotations

import ast
from pathlib import Path

import secdogie_citadel

ROOT = Path(secdogie_citadel.__file__).resolve().parent
DECISION_PATH = [*sorted((ROOT / "cortex").glob("*.py")), *sorted((ROOT / "mind").glob("*.py")), ROOT / "deliberate.py",
                 ROOT / "decision.py", ROOT / "socratic.py", ROOT / "thinker.py"]
GONE = ("lexicon", "concepts", "meaning", "perceive", "converse", "explain", "principles", "judgment")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_nothing_on_the_decision_path_matches_words():
    for path in DECISION_PATH:
        imported = _imports(path)
        assert "re" not in imported and "regex" not in imported and "fnmatch" not in imported, path.name


def test_the_word_tables_are_gone():
    for name in GONE:
        assert not (ROOT / "mind" / f"{name}.py").exists() and not (ROOT / f"{name}.py").exists(), name
    for path in DECISION_PATH:
        imported = _imports(path)
        assert not any(i.split(".")[-1] in GONE for i in imported), path.name
