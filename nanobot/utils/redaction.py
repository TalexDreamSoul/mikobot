"""Credential redaction for operator-visible diagnostic text.

This is the canonical home for credential masking. Redaction rules are a security
control whose value depends on every display surface applying the *same* rule, so
call sites import from here instead of keeping a local copy: two copies that drift
mean one surface masks a secret while another prints it, and each surface's own
tests still pass.

The rules deliberately trade a little recall for readability. Diagnostics are the
operator's only view into a failing extension, so a redactor that eats ordinary
prose ("token expired", "Secret Santa Bot") destroys the thing it is protecting.
Masking therefore requires an explicit ``name=value`` / ``name: value`` signal, a
``Bearer`` header, or a vendor credential's own recognizable prefix.
"""

from __future__ import annotations

import re

__all__ = ["redact_credentials"]

_REDACTED = "<redacted>"

# Identifier characters that make up a credential name, so that composed names such
# as ``AWS_SECRET_ACCESS_KEY`` or ``x-api-key-v2`` are recognized as one name.
_NAME_CHAR = r"[A-Za-z0-9_.\-]"
# A value runs to the first separator that can plausibly end it.
_VALUE = r"[^\s,;'\"&#]+"
# ``=`` or ``:``, tolerating the quotes around a name and value in a dict or JSON
# repr so that ``{'password': 'p@ss'}`` is recognized the same as ``password=p@ss``.
_SEPARATOR = r"['\"]?\s*[=:]\s*['\"]?"
# Names whose value is masked unconditionally.
_STRONG_NAMES = r"secret|password|passwd|credential"
# Names that also appear in numeric configuration (``max_tokens``, ``token_limit``),
# so a value that is nothing but a number stays readable.
_TOKEN_NAMES = (
    r"api[_-]?key|access[_-]?key|private[_-]?key|signing[_-]?key|token|bearer"
)
_NOT_A_BARE_NUMBER = r"(?!\d+(?:\.\d+)?(?![\w.]))"


def _named_value(names: str) -> str:
    """Build a capturing ``<name><separator>`` prefix for the given credential words.

    The name is anchored at its first character and consumed possessively, and the
    credential word is confirmed by lookahead rather than by splitting the name around
    it. Splitting would give the engine many ways to divide one long identifier, which
    turns hostile input such as ``"secret" * 700`` into a multi-second scan.
    """

    return rf"(?<!{_NAME_CHAR})((?={_NAME_CHAR}*(?:{names})){_NAME_CHAR}++{_SEPARATOR})"


_STRONG_ASSIGNMENT_RE = re.compile(
    rf"{_named_value(_STRONG_NAMES)}{_VALUE}",
    re.IGNORECASE,
)
_TOKEN_ASSIGNMENT_RE = re.compile(
    rf"{_named_value(_TOKEN_NAMES)}{_NOT_A_BARE_NUMBER}{_VALUE}",
    re.IGNORECASE,
)
# ``Authorization: Bearer <token>`` -- the one credential form where whitespace,
# rather than ``=`` or ``:``, is the standard separator.
_BEARER_RE = re.compile(rf"(?<!{_NAME_CHAR})(bearer\s+){_VALUE}", re.IGNORECASE)
# ``Authorization: Basic <base64>``. ``Bearer`` alone is handled above, but the other
# schemes carry the credential the same way, and the header name is what makes the
# match safe: a bare ``basic <word>`` is ordinary prose, ``authorization: basic`` is not.
_AUTH_HEADER_RE = re.compile(
    rf"(?<!{_NAME_CHAR})"
    rf"((?={_NAME_CHAR}*authorization){_NAME_CHAR}++\s*[=:]\s*"
    rf"(?:basic|bearer|digest)\s+){_VALUE}",
    re.IGNORECASE,
)
# A credential inside a connection URI (``postgres://user:p4ssw0rd@host/db``). Requiring
# ``user:`` before and ``@`` after keeps ordinary URLs, including ``host:port`` ones,
# untouched.
_URI_CREDENTIAL_RE = re.compile(r"(://[^\s/:@]{1,128}:)[^\s/@]{1,256}(?=@)")
# Credentials that carry their own prefix and so leak without any name next to them.
# Matched case-sensitively: the prefixes are fixed, and folding case would start
# matching ordinary words.
_KNOWN_SECRET_RE = re.compile(
    r"""(?<![A-Za-z0-9_-])(?:
          sk-[A-Za-z0-9_-]{16,}                            # OpenAI, Anthropic
        | github_pat_[A-Za-z0-9_]{16,}                     # GitHub fine-grained
        | gh[pousr]_[A-Za-z0-9]{16,}                       # GitHub classic
        | glpat-[A-Za-z0-9_-]{16,}                         # GitLab
        | xox[abopsr]-[A-Za-z0-9-]{10,}                    # Slack
        | npm_[A-Za-z0-9]{16,}                             # npm
        | AIza[A-Za-z0-9_-]{16,}                           # Google
        | (?:AKIA|ASIA|ABIA|ACCA|A3T[A-Z0-9])[A-Z0-9]{16}  # AWS access key ID
        | eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*  # JWT
    )""",
    re.VERBOSE,
)


def redact_credentials(text: str) -> str:
    """Mask credential-looking values while leaving the surrounding text readable."""

    redacted = _STRONG_ASSIGNMENT_RE.sub(rf"\1{_REDACTED}", text)
    redacted = _TOKEN_ASSIGNMENT_RE.sub(rf"\1{_REDACTED}", redacted)
    redacted = _AUTH_HEADER_RE.sub(rf"\1{_REDACTED}", redacted)
    redacted = _BEARER_RE.sub(rf"\1{_REDACTED}", redacted)
    redacted = _URI_CREDENTIAL_RE.sub(rf"\1{_REDACTED}", redacted)
    return _KNOWN_SECRET_RE.sub(_REDACTED, redacted)
