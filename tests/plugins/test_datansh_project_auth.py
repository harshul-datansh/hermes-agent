"""Project-owned Datansh credentials and multi-project identity tests."""

from types import SimpleNamespace


def test_project_password_is_hashed_and_bare_email_is_canonical(tmp_path):
    from plugins.pmo.credentials import (
        load_project_credentials,
        set_project_password,
    )

    scope = SimpleNamespace(primary_path=str(tmp_path))
    path = set_project_password(scope, "person@example.com", "project-secret")

    raw = path.read_text(encoding="utf-8")
    assert "project-secret" not in raw
    credentials = load_project_credentials(scope)
    assert [item.principal for item in credentials.credentials] == [
        "human:person@example.com"
    ]


def test_one_identity_can_verify_against_any_project_owned_hash(monkeypatch):
    from plugins.dashboard_auth.basic import hash_password
    from plugins.pmo import credentials

    records = [
        credentials.CredentialRecord(
            principal="human:person@example.com",
            password_hash=hash_password("project-one-secret"),
        ),
        credentials.CredentialRecord(
            principal="human:person@example.com",
            password_hash=hash_password("project-two-secret"),
        ),
    ]
    monkeypatch.setattr(credentials, "iter_project_credentials", lambda: iter(records))

    assert credentials.verify_project_password(
        "person@example.com", "project-two-secret"
    ) == "human:person@example.com"
    assert credentials.verify_project_password(
        "human:person@example.com", "wrong-secret"
    ) is None


def test_datansh_provider_returns_stable_human_identity(monkeypatch):
    from plugins.dashboard_auth.datansh import DatanshAuthProvider

    monkeypatch.setattr(
        "plugins.dashboard_auth.datansh.verify_project_password",
        lambda username, password: "human:person@example.com"
        if username == "person@example.com" and password == "project-secret"
        else None,
    )
    provider = DatanshAuthProvider(
        username="__datansh_project_identity__",
        password_hash="scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        secret=b"datansh-test-secret-please-change",
    )

    session = provider.complete_password_login(
        username="person@example.com", password="project-secret"
    )
    assert session.user_id == "person@example.com"
    # Hermes' base provider keeps the identity in user_id; PMO derives the
    # canonical human principal from that field.
    assert session.email == ""
    assert session.provider == "datansh"
