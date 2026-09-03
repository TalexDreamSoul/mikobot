from __future__ import annotations

import time

import pytest

from nanobot.extensions.adapters.common import safe_extension_label
from nanobot.extensions.contracts import (
    _MAX_MESSAGE_LENGTH,
    _require_public_text,
    safe_extension_message,
)


def _accepts_as_public_text(text: str) -> bool:
    """Whether the descriptor validators would let ``text`` through as a public field."""

    try:
        _require_public_text(text, "probe", maximum=_MAX_MESSAGE_LENGTH)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize(
    ("secret", "leaked"),
    [
        (
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "wJalrXUtnFEMI",
        ),
        ("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
        ("auth failed: sk-ant-api03-AbCdEf1234567890xyz", "sk-ant-api03"),
        ("bad token ghp_16CharactersXXXXXXXXXXXXXXXXXXXX", "ghp_16Characters"),
        ("token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig", "eyJhbGciOiJIUzI1NiJ9"),
        ("https://api.example.com/v1?api_key=SUPERSECRET&x=1", "SUPERSECRET"),
        ("password=hunter2", "hunter2"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcd.ef", "eyJhbGciOiJIUzI1NiJ9"),
        ("x-api-key: abc123def456ghi", "abc123def456ghi"),
        ("slack xoxb-1234567890-abcdefghij rejected", "xoxb-1234567890"),
        ("config: {'password': 'p@ssw0rd', 'secret': 'shh'}", "p@ssw0rd"),
        ('{"api_key": "abc123def456ghi"}', "abc123def456ghi"),
        ("gitlab glpat-AbCdEf1234567890xyz denied", "glpat-AbCdEf"),
        ("google AIzaSyA1234567890abcdefghij blocked", "AIzaSyA1234567890"),
        # Schemes other than Bearer carry the credential the same way.
        ("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpwYXNz"),
        ("proxy-authorization: Basic YWJjOmRlZmdoaWprbA==", "YWJjOmRlZmdo"),
        # A password embedded in a connection URI.
        ("DATABASE_URL=postgres://svc:p4ssw0rd@db.internal/app", "p4ssw0rd"),
        # Key material that is neither an API key nor named "secret".
        ("private_key=MIIEvQIBADANBgkqhkiG9w0", "MIIEvQIBADAN"),
        ("signing_key: abcdef0123456789", "abcdef0123456789"),
        ("credential=s3cr3tvalue", "s3cr3tvalue"),
    ],
)
def test_safe_extension_message_redacts_credentials(secret: str, leaked: str) -> None:
    message = safe_extension_message(secret)

    assert leaked not in message
    assert "<redacted>" in message


@pytest.mark.parametrize(
    "disclosure",
    [
        "file:///Users/alice/.nanobot/config.json",
        "\\\\server\\share\\secret.txt",
        "//server/share/x",
        "\\Users\\alice\\secret",
    ],
)
def test_safe_extension_message_redacts_previously_bypassed_paths(disclosure: str) -> None:
    message = safe_extension_message(disclosure)

    assert "alice" not in message
    assert "secret" not in message
    assert "share" not in message
    assert "<path>" in message
    assert not _accepts_as_public_text(disclosure)


@pytest.mark.parametrize(
    "disclosure",
    [
        "/Users/alice/secret",
        "~/.nanobot/config.json",
        "~alice/project",
        "C:\\Users\\alice\\secret",
        "C:/Users/alice/secret",
        '"/Users/alice/quoted secret/config.toml"',
    ],
)
def test_safe_extension_message_still_redacts_established_paths(disclosure: str) -> None:
    message = safe_extension_message(disclosure)

    assert "alice" not in message
    assert "<path>" in message
    assert not _accepts_as_public_text(disclosure)


def test_safe_extension_message_hides_the_heap_address_of_a_bare_object() -> None:
    message = safe_extension_message(object())

    assert "0x" not in message
    assert "<address>" in message


def test_safe_extension_message_redacts_before_truncating_to_display_length() -> None:
    """A secret straddling the display cut is masked, not half-printed."""

    padding = "x" * 985
    message = safe_extension_message(f"{padding} api_key=sk-AbCdEf1234567890secret")

    assert len(message) <= _MAX_MESSAGE_LENGTH
    # Truncating first would have left this leading fragment of the secret behind.
    assert "sk-AbC" not in message
    assert message.endswith("api_key=<redac")


@pytest.mark.parametrize(
    "readable",
    [
        # Prose that names a credential without disclosing one.
        "token expired",
        "no secret found",
        "invalid api key",
        "Secret Santa Bot",
        "Missing credential for the upstream service",
        # Numeric configuration that shares a name with a credential.
        "max_tokens=4096",
        "Invalid value for max_tokens: 4096",
        "timeout: 1.5",
        # Display names and prose containing slashes.
        "Feishu / Lark",
        "WeChat / WeCom",
        "and/or",
        "50/50 split",
        "Feishu // Lark",
        # URLs, which are not local paths.
        "https://example.test/extensions?source=workspace",
        "https://api.example.com/v1/models",
        # Escapes that merely look like Windows paths.
        "unexpected '\\n\\t' in the manifest",
        # Short hex that is an error code, not an address.
        "provider returned 0x8007",
        # Prose that names an auth scheme without carrying a credential.
        "basic authentication failed",
        "authorization required",
        # Connection URIs whose colon introduces a port, not a password.
        "connect to redis://cache.internal:6379/0",
        "server listening on http://127.0.0.1:8765",
    ],
)
def test_safe_extension_message_leaves_readable_diagnostics_intact(readable: str) -> None:
    assert safe_extension_message(readable) == readable


def test_safe_extension_label_keeps_display_names_containing_a_slash() -> None:
    """``Feishu / Lark`` must survive: a mangled label drops the channel from Settings."""

    assert safe_extension_label("Feishu / Lark", fallback="feishu") == "Feishu / Lark"
    assert _accepts_as_public_text("Feishu / Lark")


def test_safe_extension_message_redacts_a_secret_embedded_in_a_longer_diagnostic() -> None:
    message = safe_extension_message(
        "connect to https://example.test/v1 failed for "
        "/Users/alice/.nanobot/config.json with token=sk-AbCdEf1234567890xyz"
    )

    assert "sk-AbCdEf1234567890xyz" not in message
    assert "alice" not in message
    assert "<redacted>" in message
    assert "<path>" in message
    assert "https://example.test/v1" in message


@pytest.mark.parametrize(
    "hostile",
    [
        "secret" * 700,
        "token" * 800,
        "api_key" * 570,
        "a_" * 2000,
        "sk-" + "a" * 3990,
        "/" * 4000,
        "\\" * 4000,
        '"' * 4000,
    ],
)
def test_safe_extension_message_stays_linear_on_hostile_input(hostile: str) -> None:
    """Third-party exception text is untrusted, so redaction must not be a hang vector."""

    start = time.perf_counter()
    safe_extension_message(hostile)
    elapsed = time.perf_counter() - start

    # A name pattern that lets one long identifier be split around the credential word
    # takes tens of seconds on these inputs; the linear form takes single-digit ms.
    assert elapsed < 1.0
