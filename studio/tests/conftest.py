from __future__ import annotations

import pytest
from secdogie_studio.profile import create_profile

PASS = b"correct horse battery"


@pytest.fixture
def profile(tmp_path):
    return create_profile(tmp_path / "profile", PASS, kdf="min")
