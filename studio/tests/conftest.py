from __future__ import annotations

import os

# The tests read Studio's Chinese words; English is tested on purpose where it matters.
os.environ.setdefault("SECDOGIE_LANG", "zh")

import pytest
from secdogie_studio.profile import create_profile

PASS = b"correct horse battery"


@pytest.fixture
def profile(tmp_path):
    return create_profile(tmp_path / "profile", PASS, kdf="min")
