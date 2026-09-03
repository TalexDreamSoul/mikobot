"""Exact canonical extension target resolution for management surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from nanobot.extensions.adapters.common import canonical_extension_name
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSnapshot,
    ExtensionSource,
    extension_package_id,
)


@dataclass(frozen=True, slots=True)
class CanonicalExtensionTarget:
    """One exact registry target selected by a legacy feature name."""

    package_id: str
    target_id: str
    revision: str | None
    lifecycle: ExtensionLifecycle
    actions: frozenset[ExtensionAction]
    source: ExtensionSource


def resolve_extension_feature_target(
    snapshot: ExtensionSnapshot,
    name: str,
    instance_id: str | None = None,
) -> CanonicalExtensionTarget | None:
    """Resolve a name to one channel instance or standalone feature package.

    A channel package owns its name even if an optional feature shares it.  Its
    requested instance must resolve exactly; a missing instance never falls back
    to a sibling.  The default channel component is selected when omitted.
    """
    package_name = canonical_extension_name(name, fallback="channel")
    channel_package = _package(
        snapshot,
        extension_package_id(ExtensionSource.CHANNEL_PACKAGE, package_name),
    )
    if channel_package is not None:
        raw_instance_id = (
            "default" if instance_id is None or instance_id == "" else instance_id
        )
        component_name = canonical_extension_name(raw_instance_id, fallback="instance")
        component = next(
            (
                candidate
                for candidate in channel_package.components
                if candidate.kind is ExtensionComponentKind.CHANNEL
                and candidate.name == component_name
            ),
            None,
        )
        return _target(channel_package, component) if component is not None else None

    return resolve_extension_package_target(snapshot, name)


def resolve_extension_package_target(
    snapshot: ExtensionSnapshot,
    name: str,
    *,
    source: ExtensionSource | None = None,
) -> CanonicalExtensionTarget | None:
    """Resolve a server-selected feature name to its exact package target.

    Package actions deliberately derive identity from the known domain name rather
    than trusting a client-supplied package ID. When a source is supplied, no
    cross-source fallback is permitted.
    """
    package_name = canonical_extension_name(name, fallback="channel")
    sources = (source,) if source is not None else (
        ExtensionSource.CHANNEL_PACKAGE,
        ExtensionSource.OPTIONAL_FEATURE,
    )
    for candidate_source in sources:
        package = _package(
            snapshot, extension_package_id(candidate_source, package_name)
        )
        if package is not None:
            return _target(package)
    return None


def _package(snapshot: ExtensionSnapshot, package_id: str) -> ExtensionPackageDescriptor | None:
    return next((package for package in snapshot.packages if package.id == package_id), None)


def _target(
    package: ExtensionPackageDescriptor | None,
    component: ExtensionComponentDescriptor | None = None,
) -> CanonicalExtensionTarget | None:
    if package is None:
        return None
    descriptor = package if component is None else component
    return CanonicalExtensionTarget(
        package_id=package.id,
        target_id=descriptor.id,
        revision=descriptor.revision,
        lifecycle=descriptor.lifecycle,
        actions=descriptor.actions,
        source=package.source,
    )


__all__ = [
    "CanonicalExtensionTarget",
    "resolve_extension_feature_target",
    "resolve_extension_package_target",
]
