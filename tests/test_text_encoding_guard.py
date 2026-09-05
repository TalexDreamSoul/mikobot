"""Repository-wide guard against implicit text encoding.

Windows CI caught this the expensive way: a test read the default config, which
carries the bot icon emoji, and cp1252 could not decode it. Reading or writing
text without an explicit encoding works everywhere the maintainers develop and
fails on the one platform in the matrix that does not default to UTF-8, so the
defect is invisible until it reaches a Windows user.

The weixin channel had four such sites in production, one of them writing
account state with ``ensure_ascii=False`` -- meaning a Chinese message buffer
would be written raw and crash the encode on Windows, for the audience most
likely to have one.
"""
from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1] / "nanobot"

# Path.read_text/write_text and open() take encoding by keyword; read_text also
# accepts it positionally.
_TEXT_METHODS = {"read_text", "write_text"}


def _positional_encoding(node: ast.Call, method: str) -> bool:
    """True when encoding was passed positionally rather than by keyword."""
    # read_text(encoding) / write_text(data, encoding)
    index = 0 if method == "read_text" else 1
    return len(node.args) > index and isinstance(node.args[index], ast.Constant)


def _binary_mode(node: ast.Call) -> bool:
    """True for open() calls that never decode, so encoding does not apply."""
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return "b" in str(keyword.value.value)
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        return "b" in str(node.args[1].value)
    return False


def _offenders(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if any(keyword.arg == "encoding" for keyword in node.keywords):
            continue

        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _TEXT_METHODS:
            # os.open and zipfile members are unrelated to text decoding.
            if _positional_encoding(node, func.attr):
                continue
            found.append((node.lineno, func.attr))
        elif isinstance(func, ast.Name) and func.id == "open" and not _binary_mode(node):
            found.append((node.lineno, "open"))
    return found


def test_no_production_module_reads_or_writes_text_without_an_encoding() -> None:
    """Every text read and write in the shipped package names its encoding."""
    offenders: list[str] = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        # Tests inside channel packages ship with the wheel but never run for a
        # user; a stray implicit encoding there cannot corrupt anyone's data.
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, call in _offenders(tree):
            offenders.append(f"{path.relative_to(_PACKAGE.parent)}:{lineno} {call}()")

    assert offenders == [], (
        "text IO without an explicit encoding falls back to the locale codepage, "
        "which is cp1252 on Windows and cannot encode CJK text or emoji:\n  "
        + "\n  ".join(offenders)
    )
