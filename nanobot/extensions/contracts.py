"""Immutable contracts for the explicit extension registry foundation."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, TypeVar, cast

from nanobot.utils.redaction import redact_credentials

__all__ = [
    "ExtensionAction",
    "ExtensionActionContext",
    "ExtensionActionRequest",
    "ExtensionActionResult",
    "ExtensionAdapter",
    "ExtensionAdapterSnapshot",
    "ExtensionComponentDescriptor",
    "ExtensionComponentKind",
    "ExtensionConfigurationTarget",
    "ExtensionDiagnostic",
    "ExtensionExecution",
    "ExtensionLifecycle",
    "ExtensionPackageDescriptor",
    "ExtensionSnapshot",
    "ExtensionSource",
    "ExtensionTrust",
    "extension_component_id",
    "extension_package_id",
    "requires_risk_acknowledgement",
    "safe_extension_message",
]

_MAX_ID_SEGMENT_LENGTH = 128
_MAX_DISPLAY_NAME_LENGTH = 256
_MAX_DESCRIPTION_LENGTH = 2_000
_MAX_VERSION_LENGTH = 256
_MAX_PERMISSION_LENGTH = 256
_MAX_PERMISSIONS = 128
_MAX_CAPABILITY_LENGTH = 128
_MAX_CAPABILITIES = 256
_MAX_MESSAGE_LENGTH = 1_000
# Redaction runs before the message is cut to its display length, so that a secret
# straddling the cut is masked rather than half-printed. This bounds the text the
# redaction patterns have to scan.
_MAX_RAW_MESSAGE_LENGTH = 4 * _MAX_MESSAGE_LENGTH
_MAX_ACTION_VALUE_KEYS = 64
_MAX_ACTION_VALUE_DEPTH = 8
_MAX_ACTION_VALUE_SCALARS = 1_024
_MAX_ACTION_VALUE_NODES = 1_024
_MAX_ACTION_VALUE_TEXT_LENGTH = 2_000
_MAX_COMPONENTS = 512
_MAX_ADAPTER_PACKAGES = 1_024
_MAX_ADAPTER_DIAGNOSTICS = 256
_MAX_SNAPSHOT_PACKAGES = 4_096
_MAX_SNAPSHOT_DIAGNOSTICS = 4_096
_ID_SEGMENT = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
# CPython's default ``repr`` leaks a heap address, which discloses the ASLR layout.
_OBJECT_ADDRESS = re.compile(r"\bat 0x[0-9A-Fa-f]+\b")
# The openings that make the rest of a run a local filesystem path. Each branch is
# anchored so that a URL such as ``https://host/path`` is not mistaken for one.
_PATH_START = r"""
      (?<=://) /                             # scheme:///absolute/path
    | (?<![\w:/]) // [A-Za-z0-9._-]+ /       # //server/share
    | (?<![\w:/]) / (?=[^\s/])               # /absolute/path, but not a lone "/"
    | (?<![\w:]) ~ [A-Za-z0-9._-]* /         # ~/path and ~user/path
    | (?<![\w:]) [A-Za-z] : [\\/]            # C:\path and C:/path
    | (?<![\w:]) \\\\ [A-Za-z0-9._-]+ \\     # \\server\share
    | (?<![\w:]) \\ [A-Za-z0-9._-]{2,} \\    # \Users\alice
"""
_ABSOLUTE_PATH = re.compile(rf"(?:{_PATH_START})", re.VERBOSE)
# Quoted forms are matched whole so that a path containing spaces is redacted
# entirely rather than leaving its tail behind.
_REDACTABLE_PATH = re.compile(
    rf"""
      " (?: [A-Za-z][A-Za-z0-9+.\-]* :// )? (?: {_PATH_START} ) .*? "
    | ' (?: [A-Za-z][A-Za-z0-9+.\-]* :// )? (?: {_PATH_START} ) .*? '
    | (?: {_PATH_START} ) \S*
    """,
    re.VERBOSE,
)


class ExtensionSource(StrEnum):
    """The closed set of origins for extension packages."""

    BUILTIN = "builtin"
    AGENT_PLUGIN = "agent_plugin"
    PYTHON_ENTRY_POINT = "python_entry_point"
    WORKSPACE = "workspace"
    CONFIGURED = "configured"
    CHANNEL_PACKAGE = "channel_package"
    PROVIDER_REGISTRY = "provider_registry"
    CLI_APP = "cli_app"
    OPTIONAL_FEATURE = "optional_feature"


class ExtensionComponentKind(StrEnum):
    """The closed set of component families exposed by a package."""

    SKILL = "skill"
    MCP_SERVER = "mcp_server"
    TOOL = "tool"
    CHANNEL = "channel"
    LLM_PROVIDER = "llm_provider"
    IMAGE_PROVIDER = "image_provider"
    TRANSCRIPTION_PROVIDER = "transcription_provider"
    HOOK = "hook"
    CLI_APP = "cli_app"
    OPTIONAL_FEATURE = "optional_feature"


class ExtensionTrust(StrEnum):
    """The declared trust origin, not an enforcement mechanism."""

    FIRST_PARTY = "first_party"
    OPERATOR_TRUSTED = "operator_trusted"
    WORKSPACE_CONTENT = "workspace_content"
    REMOTE_SERVICE = "remote_service"


class ExtensionExecution(StrEnum):
    """The execution boundary declared by an extension."""

    DATA = "data"
    IN_PROCESS = "in_process"
    CHILD_PROCESS = "child_process"
    REMOTE = "remote"


class ExtensionLifecycle(StrEnum):
    """The closed lifecycle vocabulary used in registry snapshots.

    Every member here has a producer. ``disabling`` and ``changed`` were removed at
    cutover because no adapter ever reported them: every family disables
    synchronously, and a package whose fingerprint moved is reported as ``disabled``
    by the owner that invalidated it. A vocabulary member without a producer reads as
    a supported state the contract cannot actually back, so a future transient state
    is added together with the adapter that emits it.
    """

    DISCOVERED = "discovered"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    ENABLING = "enabling"
    ENABLED = "enabled"
    RELOADING = "reloading"
    FAILED = "failed"
    RESTART_REQUIRED = "restart_required"


class ExtensionAction(StrEnum):
    """The lifecycle actions which an extension may explicitly declare."""

    INSPECT = "inspect"
    CONFIGURE = "configure"
    ENABLE = "enable"
    DISABLE = "disable"
    RELOAD = "reload"
    RECONNECT = "reconnect"
    INSTALL = "install"
    UNINSTALL = "uninstall"
    RESTART_REQUIRED = "restart_required"


def _require_enum(value: object, enum_type: type[StrEnum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field_name} must be a {enum_type.__name__}")


def _require_bool(value: object, field_name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")


def _require_text(
    value: object,
    field_name: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    if _CONTROL_CHARACTER.search(value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _require_public_text(
    value: object,
    field_name: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    text = _require_text(value, field_name, maximum=maximum, allow_empty=allow_empty)
    if _ABSOLUTE_PATH.search(text):
        raise ValueError(f"{field_name} must not contain an absolute filesystem path")
    return text


def _require_id_segment(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    if not 1 <= len(value) <= _MAX_ID_SEGMENT_LENGTH:
        raise ValueError(
            f"{field_name} must contain between 1 and {_MAX_ID_SEGMENT_LENGTH} characters"
        )
    if not value.isascii() or _ID_SEGMENT.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be lowercase ASCII and use only letters, digits, '.', '_' or '-'"
        )
    if ".." in value:
        raise ValueError(f"{field_name} must not contain consecutive periods")
    return value


def _package_parts(package_id: object) -> tuple[ExtensionSource, str]:
    if not isinstance(package_id, str):
        raise ValueError("package ID must be text")
    match = re.fullmatch(r"ext:([^:/]+):([^:/]+)", package_id)
    if match is None:
        raise ValueError("package ID must use the canonical ext:<source>:<name> form")
    source_value, name = match.groups()
    try:
        source = ExtensionSource(source_value)
    except ValueError as exc:
        raise ValueError("package ID contains an unknown extension source") from exc
    _require_id_segment(name, "package name")
    if package_id != extension_package_id(source, name):
        raise ValueError("package ID must use canonical segments")
    return source, name


def _component_parts(component_id: object) -> tuple[str, ExtensionComponentKind, str]:
    if not isinstance(component_id, str):
        raise ValueError("component ID must be text")
    package_id, separator, component = component_id.partition("/")
    if not separator or not component or "/" in component:
        raise ValueError("component ID must use the canonical package/kind:name form")
    _package_parts(package_id)
    kind_value, colon, name = component.partition(":")
    if not colon or not name or ":" in name:
        raise ValueError("component ID must use the canonical package/kind:name form")
    try:
        kind = ExtensionComponentKind(kind_value)
    except ValueError as exc:
        raise ValueError("component ID contains an unknown component kind") from exc
    _require_id_segment(name, "component name")
    if component_id != extension_component_id(package_id, kind, name):
        raise ValueError("component ID must use canonical segments")
    return package_id, kind, name


def _require_target_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("target ID must be text")
    try:
        if "/" in value:
            _component_parts(value)
        else:
            _package_parts(value)
    except ValueError as exc:
        raise ValueError("target ID must be a canonical package or component ID") from exc
    return value


def _require_owner_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("diagnostic owner ID must be text")
    if value.startswith("ext:"):
        _require_target_id(value)
    else:
        _require_id_segment(value, "diagnostic owner ID")
    return value


def _bounded_items(values: object, field_name: str, maximum: int) -> tuple[object, ...]:
    try:
        iterator = iter(cast(Iterable[object], values))
    except TypeError as exc:
        raise ValueError(f"{field_name} must be iterable") from exc
    items: list[object] = []
    for _ in range(maximum + 1):
        try:
            items.append(next(iterator))
        except StopIteration:
            break
    if len(items) > maximum:
        raise ValueError(f"{field_name} must contain at most {maximum} items")
    return tuple(items)

_T = TypeVar("_T")


def _bounded_instances(
    values: object,
    field_name: str,
    maximum: int,
    item_type: type[_T],
    item_label: str,
) -> tuple[_T, ...]:
    items: list[_T] = []
    for item in _bounded_items(values, field_name, maximum):
        if not isinstance(item, item_type):
            raise ValueError(f"{field_name} must contain only {item_label} values")
        items.append(item)
    return tuple(items)


def _normalize_text_items(
    values: object,
    field_name: str,
    *,
    maximum_item_length: int,
    maximum_items: int,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be an iterable of text, not a single string")
    items = _bounded_items(values, field_name, maximum_items)
    normalized = {
        _require_public_text(item, field_name, maximum=maximum_item_length) for item in items
    }
    return tuple(sorted(normalized))


def _normalize_actions(values: object, field_name: str) -> frozenset[ExtensionAction]:
    actions = _bounded_instances(
        values,
        field_name,
        len(ExtensionAction),
        ExtensionAction,
        "ExtensionAction",
    )
    return frozenset(actions)


def extension_package_id(source: ExtensionSource, name: str) -> str:
    """Build the canonical identity for an extension package."""

    _require_enum(source, ExtensionSource, "source")
    return f"ext:{source.value}:{_require_id_segment(name, 'package name')}"


def extension_component_id(
    package_id: str,
    kind: ExtensionComponentKind,
    name: str,
) -> str:
    """Build the canonical identity for a component owned by ``package_id``."""

    _package_parts(package_id)
    _require_enum(kind, ExtensionComponentKind, "component kind")
    return f"{package_id}/{kind.value}:{_require_id_segment(name, 'component name')}"


def safe_extension_message(value: object) -> str:
    """Return one bounded diagnostic line without credential or local path disclosures."""

    try:
        message = str(value)[:_MAX_RAW_MESSAGE_LENGTH]
    except Exception:
        return "Extension operation failed."
    message = _CONTROL_CHARACTER.sub(" ", message)
    message = _OBJECT_ADDRESS.sub("at <address>", message)
    message = redact_credentials(message)
    message = _REDACTABLE_PATH.sub("<path>", message)
    message = " ".join(message.split())
    if not message:
        return "Extension operation failed."
    return message[:_MAX_MESSAGE_LENGTH]


@dataclass(frozen=True, slots=True)
class ExtensionConfigurationTarget:
    """A configuration destination described without configuration values."""

    section: str
    item: str | None = None

    def __post_init__(self) -> None:
        _require_id_segment(self.section, "configuration section")
        if self.item is not None:
            _require_id_segment(self.item, "configuration item")


@dataclass(frozen=True, slots=True)
class ExtensionDiagnostic:
    """A bounded, display-safe diagnostic associated with an adapter or target."""

    owner_id: str
    code: str
    message: str

    def __post_init__(self) -> None:
        _require_owner_id(self.owner_id)
        _require_id_segment(self.code, "diagnostic code")
        object.__setattr__(self, "message", safe_extension_message(self.message))


@dataclass(frozen=True, slots=True)
class ExtensionComponentDescriptor:
    """A static description of one package-owned extension component."""

    id: str
    package_id: str
    kind: ExtensionComponentKind
    name: str
    display_name: str
    description: str = ""
    capabilities: tuple[str, ...] = ()
    execution: ExtensionExecution = ExtensionExecution.DATA
    lifecycle: ExtensionLifecycle = ExtensionLifecycle.DISCOVERED
    revision: str | None = None
    actions: frozenset[ExtensionAction] = frozenset()
    configuration: ExtensionConfigurationTarget | None = None
    diagnostic: ExtensionDiagnostic | None = None

    def __post_init__(self) -> None:
        _component_parts(self.id)
        _package_parts(self.package_id)
        _require_enum(self.kind, ExtensionComponentKind, "component kind")
        _require_id_segment(self.name, "component name")
        _require_public_text(
            self.display_name, "component display name", maximum=_MAX_DISPLAY_NAME_LENGTH
        )
        _require_public_text(
            self.description,
            "component description",
            maximum=_MAX_DESCRIPTION_LENGTH,
            allow_empty=True,
        )
        object.__setattr__(
            self,
            "capabilities",
            _normalize_text_items(
                self.capabilities,
                "component capabilities",
                maximum_item_length=_MAX_CAPABILITY_LENGTH,
                maximum_items=_MAX_CAPABILITIES,
            ),
        )
        _require_enum(self.execution, ExtensionExecution, "component execution")
        _require_enum(self.lifecycle, ExtensionLifecycle, "component lifecycle")
        if self.revision is not None:
            _require_public_text(
                self.revision,
                "component revision",
                maximum=_MAX_VERSION_LENGTH,
                allow_empty=True,
            )
        object.__setattr__(self, "actions", _normalize_actions(self.actions, "component actions"))
        configuration = cast(object, self.configuration)
        if configuration is not None and not isinstance(
            configuration, ExtensionConfigurationTarget
        ):
            raise ValueError("component configuration must be an ExtensionConfigurationTarget")
        diagnostic = cast(object, self.diagnostic)
        if diagnostic is not None and not isinstance(diagnostic, ExtensionDiagnostic):
            raise ValueError("component diagnostic must be an ExtensionDiagnostic")
        if diagnostic is not None and diagnostic.owner_id != self.id:
            raise ValueError("component diagnostic owner ID must match the component ID")


@dataclass(frozen=True, slots=True)
class ExtensionPackageDescriptor:
    """An immutable package tree and its warning-only trust declaration."""

    id: str
    name: str
    display_name: str
    source: ExtensionSource
    trust: ExtensionTrust
    execution: ExtensionExecution
    lifecycle: ExtensionLifecycle
    description: str = ""
    version: str | None = None
    revision: str | None = None
    isolated: bool | None = None
    permissions: tuple[str, ...] = ()
    permissions_enforced: bool = False
    actions: frozenset[ExtensionAction] = frozenset()
    configuration: ExtensionConfigurationTarget | None = None
    components: tuple[ExtensionComponentDescriptor, ...] = ()
    diagnostic: ExtensionDiagnostic | None = None

    def __post_init__(self) -> None:
        _require_enum(self.source, ExtensionSource, "package source")
        _require_id_segment(self.name, "package name")
        if self.id != extension_package_id(self.source, self.name):
            raise ValueError("package ID must match its source and name")
        _require_public_text(
            self.display_name, "package display name", maximum=_MAX_DISPLAY_NAME_LENGTH
        )
        _require_enum(self.trust, ExtensionTrust, "package trust")
        _require_enum(self.execution, ExtensionExecution, "package execution")
        _require_enum(self.lifecycle, ExtensionLifecycle, "package lifecycle")
        _require_public_text(
            self.description,
            "package description",
            maximum=_MAX_DESCRIPTION_LENGTH,
            allow_empty=True,
        )
        if self.version is not None:
            _require_public_text(
                self.version, "package version", maximum=_MAX_VERSION_LENGTH, allow_empty=True
            )
        if self.revision is not None:
            _require_public_text(
                self.revision, "package revision", maximum=_MAX_VERSION_LENGTH, allow_empty=True
            )
        if self.isolated is not None:
            _require_bool(self.isolated, "package isolated")
        object.__setattr__(
            self,
            "permissions",
            _normalize_text_items(
                self.permissions,
                "package permissions",
                maximum_item_length=_MAX_PERMISSION_LENGTH,
                maximum_items=_MAX_PERMISSIONS,
            ),
        )
        _require_bool(self.permissions_enforced, "package permissions_enforced")
        if self.trust is not ExtensionTrust.FIRST_PARTY and self.permissions_enforced:
            raise ValueError("non-first-party package permissions cannot claim enforcement")
        object.__setattr__(self, "actions", _normalize_actions(self.actions, "package actions"))
        configuration = cast(object, self.configuration)
        if configuration is not None and not isinstance(
            configuration, ExtensionConfigurationTarget
        ):
            raise ValueError("package configuration must be an ExtensionConfigurationTarget")
        diagnostic = cast(object, self.diagnostic)
        if diagnostic is not None and not isinstance(diagnostic, ExtensionDiagnostic):
            raise ValueError("package diagnostic must be an ExtensionDiagnostic")
        if diagnostic is not None and diagnostic.owner_id != self.id:
            raise ValueError("package diagnostic owner ID must match the package ID")
        components = _bounded_instances(
            self.components,
            "package components",
            _MAX_COMPONENTS,
            ExtensionComponentDescriptor,
            "ExtensionComponentDescriptor",
        )
        component_ids: set[str] = set()
        for component in components:
            if component.package_id != self.id:
                raise ValueError("component package ID must match its package owner")
            expected_id = extension_component_id(self.id, component.kind, component.name)
            if component.id != expected_id:
                raise ValueError("component ID must match its package owner, kind, and name")
            if component.id in component_ids:
                raise ValueError("package must not contain duplicate component IDs")
            component_ids.add(component.id)
        object.__setattr__(
            self,
            "components",
            tuple(sorted(components, key=lambda component: component.id)),
        )
        if self.isolated is True and requires_risk_acknowledgement(self):
            raise ValueError("risk-acknowledgement packages cannot claim isolation enforcement")


@dataclass(frozen=True, slots=True)
class ExtensionAdapterSnapshot:
    """The complete side-effect-free package tree reported by one adapter."""

    adapter_name: str
    packages: tuple[ExtensionPackageDescriptor, ...] = ()
    diagnostics: tuple[ExtensionDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        _require_id_segment(self.adapter_name, "adapter name")
        packages = _bounded_instances(
            self.packages,
            "adapter snapshot packages",
            _MAX_ADAPTER_PACKAGES,
            ExtensionPackageDescriptor,
            "ExtensionPackageDescriptor",
        )
        diagnostics = _bounded_instances(
            self.diagnostics,
            "adapter snapshot diagnostics",
            _MAX_ADAPTER_DIAGNOSTICS,
            ExtensionDiagnostic,
            "ExtensionDiagnostic",
        )
        object.__setattr__(self, "packages", tuple(sorted(packages, key=lambda package: package.id)))
        object.__setattr__(
            self,
            "diagnostics",
            tuple(sorted(diagnostics, key=lambda diagnostic: (diagnostic.owner_id, diagnostic.code))),
        )


@dataclass(frozen=True, slots=True)
class ExtensionSnapshot:
    """A deterministic registry-wide extension inventory."""

    packages: tuple[ExtensionPackageDescriptor, ...]
    diagnostics: tuple[ExtensionDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        packages = _bounded_instances(
            self.packages,
            "snapshot packages",
            _MAX_SNAPSHOT_PACKAGES,
            ExtensionPackageDescriptor,
            "ExtensionPackageDescriptor",
        )
        diagnostics = _bounded_instances(
            self.diagnostics,
            "snapshot diagnostics",
            _MAX_SNAPSHOT_DIAGNOSTICS,
            ExtensionDiagnostic,
            "ExtensionDiagnostic",
        )
        object.__setattr__(self, "packages", tuple(sorted(packages, key=lambda package: package.id)))
        object.__setattr__(
            self,
            "diagnostics",
            tuple(sorted(diagnostics, key=lambda diagnostic: (diagnostic.owner_id, diagnostic.code))),
        )


@dataclass(frozen=True, slots=True)
class ExtensionActionContext:
    """The authorization facts supplied by the calling surface."""

    actor_id: str
    is_system_admin: bool
    package_install_allowed: bool = False
    channel_pairing_completed: bool = False

    def __post_init__(self) -> None:
        _require_text(self.actor_id, "actor ID", maximum=_MAX_DISPLAY_NAME_LENGTH)
        _require_bool(self.is_system_admin, "is_system_admin")
        _require_bool(self.package_install_allowed, "package_install_allowed")
        _require_bool(self.channel_pairing_completed, "channel_pairing_completed")


def _freeze_action_value(
    value: object,
    *,
    depth: int,
    scalar_count: list[int],
    node_count: list[int],
) -> object:
    if depth > _MAX_ACTION_VALUE_DEPTH:
        raise ValueError("action values exceed the maximum nesting depth")
    node_count[0] += 1
    if node_count[0] > _MAX_ACTION_VALUE_NODES:
        raise ValueError(f"action values must contain at most {_MAX_ACTION_VALUE_NODES} nodes")
    if value is None or isinstance(value, bool):
        scalar_count[0] += 1
        frozen: object = value
    elif isinstance(value, int):
        scalar_count[0] += 1
        frozen = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("action values must not contain non-finite numbers")
        scalar_count[0] += 1
        frozen = value
    elif isinstance(value, str):
        _require_text(
            value,
            "action value text",
            maximum=_MAX_ACTION_VALUE_TEXT_LENGTH,
            allow_empty=True,
        )
        scalar_count[0] += 1
        frozen = value
    elif isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        if len(mapping) > _MAX_ACTION_VALUE_KEYS:
            raise ValueError(f"action value objects must contain at most {_MAX_ACTION_VALUE_KEYS} keys")
        frozen_items: list[tuple[str, object]] = []
        for raw_key, nested_value in mapping.items():
            key = _require_text(raw_key, "action value key", maximum=_MAX_ID_SEGMENT_LENGTH)
            frozen_items.append(
                (
                    key,
                    _freeze_action_value(
                        nested_value,
                        depth=depth + 1,
                        scalar_count=scalar_count,
                        node_count=node_count,
                    ),
                )
            )
        frozen = MappingProxyType(dict(sorted(frozen_items)))
    elif isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        if len(sequence) > _MAX_ACTION_VALUE_NODES:
            raise ValueError(f"action value arrays must contain at most {_MAX_ACTION_VALUE_NODES} items")
        frozen = tuple(
            _freeze_action_value(
                nested_value,
                depth=depth + 1,
                scalar_count=scalar_count,
                node_count=node_count,
            )
            for nested_value in sequence
        )
    else:
        raise ValueError("action values must contain only JSON-compatible values")
    if scalar_count[0] > _MAX_ACTION_VALUE_SCALARS:
        raise ValueError(f"action values must contain at most {_MAX_ACTION_VALUE_SCALARS} scalar values")
    return frozen


def _freeze_action_values(values: object) -> Mapping[str, object]:
    if not isinstance(values, Mapping):
        raise ValueError("action values must be a mapping")
    mapping = cast(Mapping[object, object], values)
    if len(mapping) > _MAX_ACTION_VALUE_KEYS:
        raise ValueError(f"action values must contain at most {_MAX_ACTION_VALUE_KEYS} keys")
    scalar_count = [0]
    node_count = [0]
    frozen_items: list[tuple[str, object]] = []
    for raw_key, value in mapping.items():
        key = _require_text(raw_key, "action value key", maximum=_MAX_ID_SEGMENT_LENGTH)
        frozen_items.append(
            (
                key,
                _freeze_action_value(
                    value,
                    depth=0,
                    scalar_count=scalar_count,
                    node_count=node_count,
                ),
            )
        )
    return MappingProxyType(dict(sorted(frozen_items)))


def _empty_action_values() -> Mapping[str, object]:
    return {}


@dataclass(frozen=True, slots=True)
class ExtensionActionRequest:
    """A validated request that the registry may dispatch to an owning adapter."""

    context: ExtensionActionContext
    target_id: str
    action: ExtensionAction
    expected_revision: str | None = None
    risk_acknowledged: bool = False
    values: Mapping[str, object] = field(default_factory=_empty_action_values)

    def __post_init__(self) -> None:
        context = cast(object, self.context)
        if not isinstance(context, ExtensionActionContext):
            raise ValueError("action context must be an ExtensionActionContext")
        _require_target_id(self.target_id)
        _require_enum(self.action, ExtensionAction, "action")
        if self.expected_revision is not None:
            _require_text(
                self.expected_revision,
                "expected revision",
                maximum=_MAX_VERSION_LENGTH,
                allow_empty=True,
            )
        _require_bool(self.risk_acknowledged, "risk_acknowledged")
        object.__setattr__(self, "values", _freeze_action_values(self.values))


@dataclass(frozen=True, slots=True)
class ExtensionActionResult:
    """A normalized response reported by the adapter that received an action."""

    ok: bool
    action: ExtensionAction
    package_id: str
    target_id: str
    lifecycle: ExtensionLifecycle | None = None
    message: str = ""

    def __post_init__(self) -> None:
        _require_bool(self.ok, "action result ok")
        _require_enum(self.action, ExtensionAction, "action result action")
        _package_parts(self.package_id)
        _require_target_id(self.target_id)
        if self.lifecycle is not None:
            _require_enum(self.lifecycle, ExtensionLifecycle, "action result lifecycle")
        object.__setattr__(self, "message", safe_extension_message(self.message))


def requires_risk_acknowledgement(package: ExtensionPackageDescriptor) -> bool:
    """Whether an executable non-first-party package requires caller acknowledgement."""

    return package.trust is not ExtensionTrust.FIRST_PARTY and package.execution in {
        ExtensionExecution.IN_PROCESS,
        ExtensionExecution.CHILD_PROCESS,
    }


class ExtensionAdapter(Protocol):
    """A family-owned provider of snapshots and lifecycle-action execution."""

    @property
    def name(self) -> str: ...

    def snapshot(self) -> ExtensionAdapterSnapshot: ...

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult: ...
