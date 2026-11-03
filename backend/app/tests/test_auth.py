from __future__ import annotations

from app.core.auth import generate_member_token, hash_token, token_matches


def test_tokens_are_high_entropy_and_stored_hashed() -> None:
    first = generate_member_token()
    second = generate_member_token()

    assert first != second
    assert len(first) >= 32
    assert hash_token(first) != first
    assert len(hash_token(first)) == 64
    assert token_matches(first, hash_token(first))
    assert not token_matches(second, hash_token(first))
