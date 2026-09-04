"""Every production composition declares its long-lived hooks through the shared helper.

Option A keeps hook identity outside ``AgentLoop``, which means the declared
descriptors and the executed hook values are two uses of one literal. These tests
are what stops those two from drifting apart: a site that hands ``AgentLoop`` a
raw list, or that builds its registry without its hook bundle, fails here.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2] / "nanobot"
_HOOK_KEYWORDS = frozenset({"hooks", "hook_factories"})
_COMPOSITION_SITES = {
    "nanobot/nanobot.py": "sdk",
    "nanobot/cli/gateway_runtime.py": "gateway",
    "nanobot/cli/commands.py": "api",
    "nanobot/cli/agent.py": "cli",
}
_DECLARATION_HELPERS = frozenset(
    {"registered_hook", "registered_hook_factory", "external_hook", "external_hook_factory"}
)


@cache
def _sources() -> tuple[dict[str, ast.Module], dict[str, str]]:
    """Parse every module under ``nanobot/``, keeping unparseable files for a text check."""
    trees: dict[str, ast.Module] = {}
    unparsed: dict[str, str] = {}
    for path in sorted(_ROOT.rglob("*.py")):
        relative = path.relative_to(_ROOT.parent).as_posix()
        text = path.read_text("utf-8")
        try:
            trees[relative] = ast.parse(text)
        except SyntaxError:
            unparsed[relative] = text
    return trees, unparsed


def _calls(tree: ast.Module, attribute: str | None = None, name: str | None = None):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if attribute is not None and isinstance(func, ast.Attribute) and func.attr == attribute:
            yield node
        elif name is not None and isinstance(func, ast.Name) and func.id == name:
            yield node


def _hook_keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg in _HOOK_KEYWORDS}


def _bundle_names(tree: ast.Module) -> set[str]:
    """Names bound directly from a ``long_lived_hooks(...)`` call."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)):
            continue
        if value.func.id != "long_lived_hooks":
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def test_exactly_four_production_sites_configure_long_lived_hooks() -> None:
    """A fifth composition cannot appear without being declared here."""
    trees, unparsed = _sources()
    sites = {
        relative
        for relative, tree in trees.items()
        for call in _calls(tree, attribute="from_config")
        if _hook_keywords(call)
    }

    assert sites == set(_COMPOSITION_SITES)
    for relative, text in unparsed.items():
        assert "hook_factories" not in text, relative
        assert "hooks=" not in text, relative


@pytest.mark.parametrize("relative", sorted(_COMPOSITION_SITES))
def test_site_hands_agent_loop_only_the_shared_bundle(relative: str) -> None:
    """No production site passes a raw hook literal to ``AgentLoop.from_config``."""
    trees, _ = _sources()
    tree = trees[relative]
    bundles = _bundle_names(tree)

    assert len(bundles) == 1, relative
    bundle = next(iter(bundles))
    observed: list[tuple[str, str]] = []
    for call in _calls(tree, attribute="from_config"):
        for keyword, value in _hook_keywords(call).items():
            assert isinstance(value, ast.Attribute), (relative, ast.unparse(value))
            assert isinstance(value.value, ast.Name), (relative, ast.unparse(value))
            assert value.value.id == bundle, (relative, ast.unparse(value))
            assert value.attr == keyword, (relative, ast.unparse(value))
            observed.append((keyword, value.attr))

    assert sorted(observed) == [("hook_factories", "hook_factories"), ("hooks", "hooks")]


@pytest.mark.parametrize("relative", sorted(_COMPOSITION_SITES))
def test_site_registers_the_same_bundle_it_executes(relative: str) -> None:
    """The registry receives the exact bundle the loop was given, not a second literal."""
    trees, _ = _sources()
    tree = trees[relative]
    bundle = next(iter(_bundle_names(tree)))
    registry_calls = list(_calls(tree, name="build_core_extension_registry"))

    assert len(registry_calls) == 1, relative
    passed = {kw.arg: kw.value for kw in registry_calls[0].keywords}
    assert "hooks" in passed, relative
    value = passed["hooks"]
    assert isinstance(value, ast.Name) and value.id == bundle, ast.unparse(value)


@pytest.mark.parametrize("relative", sorted(_COMPOSITION_SITES))
def test_site_declares_a_distinct_composition_with_literal_identities(relative: str) -> None:
    """Each site names itself once and gives every hook an explicit literal identity."""
    trees, _ = _sources()
    tree = trees[relative]
    calls = list(_calls(tree, name="long_lived_hooks"))

    assert len(calls) == 1, relative
    call = calls[0]
    composition = call.args[0]
    assert isinstance(composition, ast.Constant), ast.unparse(composition)
    assert composition.value == _COMPOSITION_SITES[relative]

    for declaration in call.args[1:]:
        assert isinstance(declaration, ast.Call), ast.unparse(declaration)
        func = declaration.func
        assert isinstance(func, ast.Name) and func.id in _DECLARATION_HELPERS
        if func.id.startswith("registered_"):
            identity = declaration.args[0]
            assert isinstance(identity, ast.Constant), ast.unparse(identity)
            assert isinstance(identity.value, str)


def test_composition_names_are_unique_across_production_sites() -> None:
    """Two processes never publish the same hook package identity."""
    assert len(set(_COMPOSITION_SITES.values())) == len(_COMPOSITION_SITES)


def test_api_usage_capture_hooks_stay_per_turn_and_uninventoried() -> None:
    """The API server's usage-capture hook reaches ``process_direct``, never a composition."""
    trees, _ = _sources()
    server = trees["nanobot/api/server.py"]

    assert not [call for call in _calls(server, attribute="from_config") if _hook_keywords(call)]
    per_turn = [call for call in _calls(server, attribute="process_direct") if _hook_keywords(call)]
    assert len(per_turn) == 1
    assert set(_hook_keywords(per_turn[0])) == {"hooks"}


def test_sdk_per_run_hooks_stay_per_turn_and_uninventoried() -> None:
    """SDK-capture and per-run caller hooks are turn values, not registered hooks."""
    trees, _ = _sources()
    sdk = trees["nanobot/nanobot.py"]
    per_turn = [call for call in _calls(sdk, attribute="process_direct") if _hook_keywords(call)]

    assert per_turn
    for call in per_turn:
        keywords = _hook_keywords(call)
        assert set(keywords) == {"hooks"}
        assert ast.unparse(keywords["hooks"]) == "per_run_hooks"
