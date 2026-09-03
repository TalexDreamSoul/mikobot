"""Optional dependency inspection and installation primitives."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any, cast

from loguru import logger
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


class OptionalFeatureError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class InstallResult:
    ok: bool
    label: str
    pip_cmd: list[str]
    failed_cmd: list[str] | None = None
    output: str = ""


@dataclass(frozen=True, slots=True)
class ChannelDependencyPreparation:
    """Safe outcome of preparing one channel manifest's dependencies."""

    ready: bool
    installed: bool = False
    message: str = ""


_INSTALL_TIMEOUT_SECONDS = 300
_LOG_OUTPUT_LIMIT = 4000
_HIDDEN_OPTIONAL_FEATURES = {"documents", "pdf"}
_BUNDLED_FEATURE_ALIASES = {"documents", "pdf"}

def hidden_optional_feature_names() -> frozenset[str]:
    """Return optional groups intentionally absent from feature inventories."""
    return frozenset({"dev", *_HIDDEN_OPTIONAL_FEATURES, *_BUNDLED_FEATURE_ALIASES})


def load_pyproject(path: Path) -> dict[str, Any]:
    import tomllib

    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return tomllib.loads(content)


def optional_dependency_groups_from_metadata() -> dict[str, list[str] | None]:
    from importlib.metadata import metadata, requires

    try:
        extras = metadata("nanobot-ai").get_all("Provides-Extra") or []
        raw_requirements = requires("nanobot-ai") or []
    except PackageNotFoundError:
        return {}
    groups: dict[str, list[str] | None] = {name: [] for name in extras if name != "dev"}
    for raw in raw_requirements:
        req = Requirement(raw)
        if not req.marker:
            continue
        for extra, deps in groups.items():
            if deps is not None and req.marker.evaluate({"extra": extra}):
                deps.append(raw)
    return groups


def optional_dependency_groups() -> dict[str, list[str] | None]:
    root = Path(__file__).resolve().parents[1]
    project = load_pyproject(root / "pyproject.toml").get("project", {})
    deps = project.get("optional-dependencies", {})
    if isinstance(deps, dict) and deps:
        return {
            name: list(cast(list[str], values))
            for name, values in cast(dict[str, object], deps).items()
            if name != "dev" and name not in _HIDDEN_OPTIONAL_FEATURES and isinstance(values, list)
        }
    return {
        name: values
        for name, values in optional_dependency_groups_from_metadata().items()
        if name not in _HIDDEN_OPTIONAL_FEATURES
    }


def _install_requirements_for_extra(extra: str, deps: list[str]) -> list[str]:
    install_args: list[str] = []
    for raw in deps:
        req = Requirement(raw)
        if req.marker and not req.marker.evaluate({"extra": extra}):
            continue
        req.marker = None
        install_args.append(str(req))
    return install_args


def install_args_for_extra(
    extra: str,
    deps: list[str] | None,
) -> tuple[list[str], str]:
    if deps:
        install_args = _install_requirements_for_extra(extra, deps)
        if install_args:
            return install_args, f"{extra} support"
        return [], f"{extra} support"
    target = f"nanobot-ai[{extra}]"
    return [target], f'"{target}"'


def _requirement_installed(req: Requirement, extra: str, seen: set[tuple[str, str]]) -> bool:
    if req.marker and not req.marker.evaluate({"extra": extra}):
        return True
    key = (
        canonicalize_name(req.name),
        ",".join(sorted(canonicalize_name(value) for value in req.extras)),
    )
    if key in seen:
        return True
    seen.add(key)
    try:
        dist = distribution(req.name)
    except PackageNotFoundError:
        return False
    if req.specifier and not req.specifier.contains(dist.version, prereleases=True):
        return False

    for requested_extra in req.extras:
        if not _extra_dependencies_installed(dist, requested_extra, seen):
            return False
    return True


def _extra_dependencies_installed(
    dist: Any,
    requested_extra: str,
    seen: set[tuple[str, str]],
) -> bool:
    normalized = canonicalize_name(requested_extra)
    provided = {
        canonicalize_name(value)
        for value in cast(list[str], dist.metadata.get_all("Provides-Extra") or [])
    }
    if provided and normalized not in provided:
        return False

    matched = False
    for raw in cast(list[str], dist.requires or []):
        req = Requirement(raw)
        if req.marker and not req.marker.evaluate({"extra": requested_extra}):
            continue
        matched = True
        if not _requirement_installed(req, requested_extra, seen):
            return False
    return matched or bool(provided)


def requirement_installed(raw: str, extra: str = "") -> bool:
    return _requirement_installed(Requirement(raw), extra, set())


def extra_installed(extra: str, deps: list[str] | None) -> bool:
    if deps is None:
        return True
    return all(requirement_installed(dep, extra) for dep in deps)


def run_install_command(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        message = f"Timed out after {_INSTALL_TIMEOUT_SECONDS}s"
        stderr = "\n".join(part for part in ((stderr or "").rstrip(), message) if part)
        return subprocess.CompletedProcess(argv, 124, stdout=stdout or "", stderr=stderr)


def command_text(argv: list[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in argv])


def _log_completed_command(label: str, proc: subprocess.CompletedProcess[str]) -> None:
    logger.info("{} exited with code {}", label, proc.returncode)
    output = (proc.stderr or proc.stdout or "").strip()
    if output:
        logger.info("{} output:\n{}", label, output[:_LOG_OUTPUT_LIMIT])


def missing_pip(proc: subprocess.CompletedProcess[str]) -> bool:
    return "no module named pip" in f"{proc.stdout}\n{proc.stderr}".lower()


def install_extra(
    extra: str,
    deps: list[str] | None,
    *,
    runner: Any = run_install_command,
) -> InstallResult:
    import importlib

    install_args, label = install_args_for_extra(extra, deps)
    pip_cmd = [sys.executable, "-m", "pip", "install", *install_args]
    if not install_args:
        logger.info("Optional feature '{}' has no installable dependencies for this platform", extra)
        return InstallResult(True, label, pip_cmd)

    logger.info("Installing optional feature '{}': {}", extra, command_text(pip_cmd))
    proc = runner(pip_cmd)
    _log_completed_command(f"Optional feature '{extra}' install", proc)
    if proc.returncode == 0:
        importlib.invalidate_caches()
        return InstallResult(True, label, pip_cmd)

    failed_cmd = pip_cmd
    failed_proc = proc
    if missing_pip(proc):
        if shutil.which("uv"):
            uv_cmd = ["uv", "pip", "install", "--python", sys.executable, *install_args]
            uv_env = os.environ.copy()
            if index_url := os.environ.get("PIP_INDEX_URL", "").strip():
                uv_env["UV_INDEX_URL"] = index_url
            logger.info("pip missing while installing '{}'; running {}", extra, command_text(uv_cmd))
            uv_proc = runner(uv_cmd, env=uv_env)
            _log_completed_command(f"Optional feature '{extra}' uv install", uv_proc)
            if uv_proc.returncode == 0:
                importlib.invalidate_caches()
                return InstallResult(True, label, pip_cmd)
            output = (uv_proc.stderr or uv_proc.stdout or "").strip()
            return InstallResult(False, label, pip_cmd, failed_cmd=uv_cmd, output=output)

        ensure_cmd = [sys.executable, "-m", "ensurepip", "--upgrade"]
        logger.info("pip missing while installing '{}'; running {}", extra, command_text(ensure_cmd))
        ensure_proc = runner(ensure_cmd)
        _log_completed_command(f"Optional feature '{extra}' ensurepip", ensure_proc)
        if ensure_proc.returncode == 0:
            logger.info("Retrying optional feature '{}': {}", extra, command_text(pip_cmd))
            proc = runner(pip_cmd)
            _log_completed_command(f"Optional feature '{extra}' install retry", proc)
            if proc.returncode == 0:
                importlib.invalidate_caches()
                return InstallResult(True, label, pip_cmd)
            failed_cmd = pip_cmd
            failed_proc = proc
        else:
            failed_cmd = ensure_cmd
            failed_proc = ensure_proc

    output = (failed_proc.stderr or failed_proc.stdout or "").strip()
    return InstallResult(False, label, pip_cmd, failed_cmd=failed_cmd, output=output)



def prepare_channel_dependencies(
    name: str,
    dependencies: list[str] | None,
    *,
    allow_install: bool,
    runner: Any = run_install_command,
) -> ChannelDependencyPreparation:
    """Ensure one channel's declared dependencies are ready without exposing installer detail."""
    if not dependencies or extra_installed(name, dependencies):
        return ChannelDependencyPreparation(ready=True)
    if not allow_install:
        return ChannelDependencyPreparation(
            ready=False,
            message="Channel dependency installation is disabled by policy.",
        )

    result = install_extra(name, dependencies, runner=runner)
    if not result.ok:
        return ChannelDependencyPreparation(
            ready=False,
            message="Channel dependencies could not be installed. Check gateway logs.",
        )
    if extra_installed(name, dependencies):
        return ChannelDependencyPreparation(ready=True, installed=True)
    return ChannelDependencyPreparation(
        ready=False,
        installed=True,
        message="Channel dependencies could not be installed. Check gateway logs.",
    )


def ensure_enabled_channel_dependencies(
    enabled_names: set[str],
    plugins: dict[str, Any],
    *,
    runner: Any = run_install_command,
) -> dict[str, str]:
    """Prepare requirements declared by enabled channel manifests.

    Returns user-safe errors keyed by channel name. Detailed installer output
    remains in gateway logs.
    """
    failures: dict[str, str] = {}
    for name in sorted(enabled_names):
        plugin = plugins.get(name)
        if plugin is None:
            continue
        preparation = prepare_channel_dependencies(
            name,
            list(plugin.dependencies),
            allow_install=True,
            runner=runner,
        )
        if preparation.ready:
            continue
        failures[name] = preparation.message
        logger.error("Could not prepare dependencies for enabled channel '{}'", name)
    return failures


def optional_feature_requires_restart(name: str) -> bool:
    """Return whether an installed feature needs the running engine rebuilt."""
    # These libraries are imported lazily or used by a newly spawned service.
    return name not in {"api", "documents", "pdf", "olostep"}
