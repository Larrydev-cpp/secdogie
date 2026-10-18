"""Task schema and the one invariant that matters: a node task carries public
work only -- no credential ever rides along."""

from __future__ import annotations

import pytest
from secdogie_identity import Identity, verify_payload
from secdogie_swarm import (
    PROTOCOL,
    TaskError,
    assert_public,
    build_crawl,
    output_digest,
    sign_task,
    validate_task,
)


def a_crawl(**over):
    kw = dict(
        task_id="t-1",
        url="https://docs.example.com/",
        allowed_origins=["https://docs.example.com"],
        issued_at_ms=1000,
        deadline_ms=10000,
        redundancy=3,
        epoch=1,
    )
    kw.update(over)
    return build_crawl(**kw)


def test_build_crawl_is_public_and_valid() -> None:
    task = a_crawl()
    validate_task(task)
    assert_public(task)
    assert task["protocol"] == PROTOCOL
    assert task["spec"] == {"url": "https://docs.example.com/", "allowed_origins": ["https://docs.example.com"]}


def test_spec_field_set_is_exact() -> None:
    task = a_crawl()
    task["spec"]["extra"] = "x"
    with pytest.raises(TaskError):
        validate_task(task)


@pytest.mark.parametrize(
    "secret_key",
    ["api_key", "apikey", "authToken", "cookie", "session_id", "operator_secret", "password", "bearer"],
)
def test_assert_public_refuses_secret_shaped_keys(secret_key: str) -> None:
    # However a credential tried to sneak in -- top level or nested -- it is refused.
    task = a_crawl()
    task["spec"]["allowed_origins"] = [{"origin": "https://x", secret_key: "sk-123"}]
    with pytest.raises(TaskError):
        assert_public(task)


def test_signed_task_still_passes_public_check() -> None:
    # The signature envelope (signer/sig) is public and must not trip the guard.
    core = Identity.generate()
    signed = sign_task(core, a_crawl())
    assert_public(signed)
    ok, signer = verify_payload(signed)
    assert ok and signer == core.did


def test_output_digest_is_order_independent() -> None:
    assert output_digest({"a": 1, "b": [2, 3]}) == output_digest({"b": [2, 3], "a": 1})
    assert output_digest({"a": 1}) != output_digest({"a": 2})
