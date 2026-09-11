import uuid

from rsi_boot.core.models import generate_feedback_token, verify_feedback_token


def test_token_roundtrip():
    rid = uuid.uuid4()
    token = generate_feedback_token(rid, "user@x.com", "secret")
    assert verify_feedback_token(token, rid, "user@x.com", "secret")


def test_token_rejects_wrong_secret():
    rid = uuid.uuid4()
    token = generate_feedback_token(rid, "user@x.com", "secret")
    assert not verify_feedback_token(token, rid, "user@x.com", "other-secret")


def test_token_rejects_wrong_user():
    rid = uuid.uuid4()
    token = generate_feedback_token(rid, "user@x.com", "secret")
    assert not verify_feedback_token(token, rid, "attacker@x.com", "secret")


def test_token_rejects_malformed():
    assert not verify_feedback_token("", uuid.uuid4(), "u", "s")
    assert not verify_feedback_token("no-dash", uuid.uuid4(), "u", "s")
