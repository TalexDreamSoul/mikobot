"""Provider family projection: grouping, safety, classification, and regressions."""

from __future__ import annotations

import socket
from collections.abc import Iterator, Mapping
from pathlib import Path

import httpx
import pytest

from nanobot.audio import transcription_registry
from nanobot.audio.transcription_registry import (
    TRANSCRIPTION_PROVIDERS,
    TranscriptionProviderSpec,
    resolve_transcription_provider,
    transcription_provider_names,
)
from nanobot.config.schema import Config
from nanobot.extensions.adapters import providers as providers_adapter
from nanobot.extensions.adapters.providers import ProviderRegistryExtensionAdapter
from nanobot.extensions.contracts import (
    ExtensionAction,
    ExtensionComponentDescriptor,
    ExtensionComponentKind,
    ExtensionLifecycle,
    ExtensionPackageDescriptor,
    ExtensionSource,
    ExtensionTrust,
    extension_package_id,
    requires_risk_acknowledgement,
)
from nanobot.providers import image_generation
from nanobot.providers.image_generation import image_gen_provider_names
from nanobot.providers.registry import PROVIDERS, ProviderSpec
from nanobot.webui.settings_models import provider_configured_for_settings

_CANONICAL_SPECS = tuple(spec for spec in PROVIDERS if not spec.settings_alias_for)


def _config(workspace: Path) -> Config:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    return config


def _adapter(config: Config, **kwargs: object) -> ProviderRegistryExtensionAdapter:
    return ProviderRegistryExtensionAdapter(lambda: config, **kwargs)  # type: ignore[arg-type]


def _packages(config: Config) -> dict[str, ExtensionPackageDescriptor]:
    return {package.name: package for package in _adapter(config).snapshot().packages}


def _component(
    package: ExtensionPackageDescriptor,
    kind: ExtensionComponentKind,
) -> ExtensionComponentDescriptor | None:
    return next((c for c in package.components if c.kind is kind), None)


def _descriptor_text(package: ExtensionPackageDescriptor) -> Iterator[str]:
    """Yield every public string a package or its components can display."""
    rows: list[ExtensionPackageDescriptor | ExtensionComponentDescriptor] = [
        package,
        *package.components,
    ]
    for row in rows:
        yield row.id
        yield row.name
        yield row.display_name
        yield row.description
        yield row.revision or ""
        yield from row.capabilities if isinstance(row, ExtensionComponentDescriptor) else ()
        yield from row.permissions if isinstance(row, ExtensionPackageDescriptor) else ()
        if row.configuration is not None:
            yield row.configuration.section
            yield row.configuration.item or ""
        if row.diagnostic is not None:
            yield row.diagnostic.message


def test_every_family_appears_once_without_id_collision(tmp_path: Path) -> None:
    """Overlapping registry names collapse into one package per family, not colliding rows."""
    snapshot = _adapter(_config(tmp_path)).snapshot()
    package_ids = [package.id for package in snapshot.packages]
    image_names = set(image_gen_provider_names())
    transcription_names = set(transcription_provider_names())
    llm_names = {spec.name for spec in PROVIDERS}

    # The overlap is total in one direction, which is why per-registry packages cannot work.
    assert image_names - llm_names == set()
    assert transcription_names - llm_names == set()
    assert image_names & llm_names
    assert transcription_names & llm_names

    assert len(package_ids) == len(set(package_ids)) == len(_CANONICAL_SPECS)
    assert set(package_ids) == {
        extension_package_id(ExtensionSource.PROVIDER_REGISTRY, spec.name)
        for spec in _CANONICAL_SPECS
    }
    component_ids = [c.id for package in snapshot.packages for c in package.components]
    assert len(component_ids) == len(set(component_ids))
    assert snapshot.diagnostics == ()


@pytest.mark.parametrize("shared_name", sorted(set(image_gen_provider_names())))
def test_shared_image_name_is_one_package_with_both_components(
    shared_name: str,
    tmp_path: Path,
) -> None:
    """Every image provider name is also an LLM name and shares a single family package."""
    package = _packages(_config(tmp_path))[shared_name]
    kinds = {component.kind for component in package.components}
    assert ExtensionComponentKind.IMAGE_PROVIDER in kinds
    assert ExtensionComponentKind.LLM_PROVIDER in kinds


@pytest.mark.parametrize("shared_name", sorted(set(transcription_provider_names())))
def test_shared_transcription_name_shares_its_family_package(
    shared_name: str,
    tmp_path: Path,
) -> None:
    """Transcription names never create a second package for the same family."""
    package = _packages(_config(tmp_path))[shared_name]
    assert _component(package, ExtensionComponentKind.TRANSCRIPTION_PROVIDER) is not None


def test_alias_specs_group_into_their_canonical_family(tmp_path: Path) -> None:
    """`opencode_zen` is a member of `opencode`, matching the existing Settings grouping."""
    packages = _packages(_config(tmp_path))
    assert "opencode_zen" not in packages
    assert extension_package_id(ExtensionSource.PROVIDER_REGISTRY, "opencode_zen") not in {
        package.id for package in packages.values()
    }
    llm = _component(packages["opencode"], ExtensionComponentKind.LLM_PROVIDER)
    assert llm is not None
    assert "alias:opencode_zen" in llm.capabilities


def test_transcription_only_family_contributes_no_llm_component(tmp_path: Path) -> None:
    """The one transcription-only spec is excluded from LLM paths here as it is in config."""
    assemblyai = _packages(_config(tmp_path))["assemblyai"]
    assert [component.kind for component in assemblyai.components] == [
        ExtensionComponentKind.TRANSCRIPTION_PROVIDER
    ]
    transcription = _component(assemblyai, ExtensionComponentKind.TRANSCRIPTION_PROVIDER)
    assert transcription is not None
    assert "transcription-only" in transcription.capabilities


def test_families_without_a_capability_omit_that_component(tmp_path: Path) -> None:
    """A family owns only the capability components its registries actually implement."""
    packages = _packages(_config(tmp_path))
    image_names = set(image_gen_provider_names())
    transcription_names = set(transcription_provider_names())
    for spec in _CANONICAL_SPECS:
        kinds = {component.kind for component in packages[spec.name].components}
        assert (ExtensionComponentKind.IMAGE_PROVIDER in kinds) is (spec.name in image_names)
        assert (ExtensionComponentKind.TRANSCRIPTION_PROVIDER in kinds) is (
            spec.name in transcription_names
        )
        assert (ExtensionComponentKind.LLM_PROVIDER in kinds) is not spec.is_transcription_only


def test_dynamic_user_defined_providers_are_not_registry_packages(tmp_path: Path) -> None:
    """A runtime `create_dynamic_spec` provider is never a PROVIDER_REGISTRY package."""
    config = Config.model_validate(
        {
            "agents": {"defaults": {"workspace": str(tmp_path)}},
            "providers": {"company_proxy": {"apiBase": "https://proxy.invalid/v1"}},
        }
    )
    packages = _packages(config)
    assert "company_proxy" not in packages
    assert {package.name for package in packages.values()} == {
        spec.name for spec in _CANONICAL_SPECS
    }


def test_discovery_builds_no_client_and_opens_no_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Projection reads static registries only: no adapter import, no client, no socket."""

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("provider discovery must not construct a client or connect")

    monkeypatch.setattr(TranscriptionProviderSpec, "load_adapter", _forbidden)
    monkeypatch.setattr(transcription_registry, "import_module", _forbidden)
    monkeypatch.setattr(image_generation, "get_image_gen_provider", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(httpx, "Client", _forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", _forbidden)

    snapshot = _adapter(_config(tmp_path)).snapshot()

    assert len(snapshot.packages) == len(_CANONICAL_SPECS)
    assert snapshot.diagnostics == ()


def test_provider_packages_are_registry_metadata_not_installed_executable_code(
    tmp_path: Path,
) -> None:
    """Provider rows never claim in-process operator code, so no risk warning applies."""
    for package in _packages(_config(tmp_path)).values():
        assert package.source is ExtensionSource.PROVIDER_REGISTRY
        assert package.isolated is None
        assert package.permissions_enforced is False
        assert package.permissions == ()
        assert requires_risk_acknowledgement(package) is False


def test_trust_and_execution_derive_from_spec_flags_not_vendor_names(tmp_path: Path) -> None:
    """Locally deployed families are operator-trusted; hosted families are remote services."""
    packages = _packages(_config(tmp_path))
    for spec in _CANONICAL_SPECS:
        package = packages[spec.name]
        expected = (
            ExtensionTrust.OPERATOR_TRUSTED if spec.is_local else ExtensionTrust.REMOTE_SERVICE
        )
        assert package.trust is expected
    assert packages["ollama"].trust is ExtensionTrust.OPERATOR_TRUSTED
    assert packages["openai"].trust is ExtensionTrust.REMOTE_SERVICE


@pytest.mark.parametrize(
    ("flag", "capability"),
    [
        ("is_gateway", "gateway"),
        ("is_local", "local-deployment"),
        ("is_oauth", "oauth"),
        ("is_direct", "direct-endpoint"),
        ("is_transcription_only", "transcription-only"),
    ],
)
def test_structural_capabilities_track_spec_flags(
    flag: str,
    capability: str,
    tmp_path: Path,
) -> None:
    """Gateway, local, OAuth, direct, and transcription-only come from flags, not name matching."""
    packages = _packages(_config(tmp_path))
    for spec in _CANONICAL_SPECS:
        component = packages[spec.name].components[0]
        assert (capability in component.capabilities) is bool(getattr(spec, flag))


def test_configured_state_matches_the_settings_configured_check(tmp_path: Path) -> None:
    """The duplicated predicate agrees with the Settings owner for every registry spec."""

    def oauth_status(spec: ProviderSpec) -> Mapping[str, object]:
        return {"configured": spec.name == "github_copilot"}

    config = _config(tmp_path)
    config.providers.openai.api_key = "sk-live"
    config.providers.ollama.api_base = "http://127.0.0.1:11434/v1"
    config.providers.azure_openai.api_base = "https://tenant.invalid/openai"
    config.providers.bedrock.region = "us-east-1"
    packages = _packages_with(config, oauth_status=oauth_status)

    for spec in _CANONICAL_SPECS:
        provider_config = getattr(config.providers, spec.name)
        expected = provider_configured_for_settings(spec, provider_config, oauth_status)
        lifecycle = packages[spec.name].lifecycle
        assert lifecycle is (
            ExtensionLifecycle.ENABLED if expected else ExtensionLifecycle.DISCOVERED
        ), spec.name


def _packages_with(config: Config, **kwargs: object) -> dict[str, ExtensionPackageDescriptor]:
    return {package.name: package for package in _adapter(config, **kwargs).snapshot().packages}


def test_oauth_configuration_is_unknown_without_an_injected_reader(tmp_path: Path) -> None:
    """No OAuth token storage is probed, so no configured claim is made for those families."""
    packages = _packages(_config(tmp_path))
    for spec in _CANONICAL_SPECS:
        if not spec.is_oauth:
            continue
        component = packages[spec.name].components[0]
        assert "configuration-unknown" in component.capabilities
        assert packages[spec.name].lifecycle is ExtensionLifecycle.DISCOVERED
    assert "configuration-unknown" not in packages["openai"].components[0].capabilities


def test_missing_provider_configuration_reports_unavailable(tmp_path: Path) -> None:
    """A family whose configuration section cannot be read is unavailable, not unconfigured."""

    class _Providers:
        def __getattr__(self, _name: str) -> object:
            return None

    config = _config(tmp_path)
    object.__setattr__(config, "providers", _Providers())
    packages = _packages(config)

    assert packages["openai"].lifecycle is ExtensionLifecycle.UNAVAILABLE
    assert "configuration-unknown" not in packages["openai"].components[0].capabilities


def test_revision_is_stable_across_credential_value_changes(tmp_path: Path) -> None:
    """Rotating a key keeps the revision; toggling configured state changes it."""
    config = _config(tmp_path)
    config.providers.openai.api_key = "sk-first"
    first = _packages(config)["openai"]
    config.providers.openai.api_key = "sk-second-and-much-longer"
    second = _packages(config)["openai"]
    config.providers.openai.api_key = ""
    unconfigured = _packages(config)["openai"]

    assert first.revision == second.revision
    assert [c.revision for c in first.components] == [c.revision for c in second.components]
    assert unconfigured.revision != first.revision


def test_revision_ignores_endpoint_proxy_and_header_configuration(tmp_path: Path) -> None:
    """Only structural facts and the safe configured boolean feed the digest."""
    config = _config(tmp_path)
    config.providers.openai.api_key = "sk-live"
    baseline = _packages(config)["openai"].revision
    config.providers.openai.api_base = "https://relay.invalid/v1"
    config.providers.openai.proxy = "http://proxy.invalid:8080"
    config.providers.openai.extra_headers = {"X-Tenant": "acme"}

    assert _packages(config)["openai"].revision == baseline


def test_descriptors_carry_no_secret_endpoint_or_path(tmp_path: Path) -> None:
    """No key, hint, endpoint, proxy, header, model, or OAuth identity reaches a descriptor."""
    secrets = (
        "sk-super-secret-key",
        "https://relay.invalid/v1",
        "http://proxy.invalid:8080",
        "acme-tenant-header",
        "gpt-image-secret-model",
        "us-west-2",
        "operator-profile",
    )
    config = _config(tmp_path)
    config.providers.openai.api_key = secrets[0]
    config.providers.openai.api_base = secrets[1]
    config.providers.openai.proxy = secrets[2]
    config.providers.openai.extra_headers = {"X-Tenant": secrets[3]}
    config.providers.bedrock.region = secrets[5]
    config.providers.bedrock.profile = secrets[6]
    config.tools.image_generation.enabled = True
    config.tools.image_generation.provider = "openai"
    config.tools.image_generation.model = secrets[4]

    text = "\n".join(
        value
        for package in _adapter(config).snapshot().packages
        for value in _descriptor_text(package)
    )

    for secret in secrets:
        assert secret not in text
    assert secrets[0][:4] not in text  # not even a masked key hint
    for spec in PROVIDERS:
        if spec.default_api_base:
            assert spec.default_api_base not in text
    assert str(tmp_path) not in text


def test_one_malformed_family_yields_a_bounded_local_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single broken family is dropped with one diagnostic; the rest are untouched."""
    real_flags = providers_adapter._structural_flags

    def flaky(spec: ProviderSpec) -> tuple[str, ...]:
        if spec.name == "openai":
            raise RuntimeError(f"boom for {tmp_path}")
        return real_flags(spec)

    monkeypatch.setattr(providers_adapter, "_structural_flags", flaky)
    snapshot = _adapter(_config(tmp_path)).snapshot()

    assert "openai" not in {package.name for package in snapshot.packages}
    assert len(snapshot.packages) == len(_CANONICAL_SPECS) - 1
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == [
        "provider_projection_failed"
    ]
    assert snapshot.diagnostics[0].owner_id == "provider-registry"
    assert str(tmp_path) not in snapshot.diagnostics[0].message


def test_unreadable_configuration_drops_the_whole_adapter_snapshot(tmp_path: Path) -> None:
    """A failed config load reports one diagnostic instead of a half-built inventory."""

    def broken_loader() -> Config:
        raise RuntimeError("config unavailable")

    snapshot = ProviderRegistryExtensionAdapter(broken_loader).snapshot()

    assert snapshot.packages == ()
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == [
        "provider_config_unavailable"
    ]


def test_configuration_destinations_are_stable_route_keys(tmp_path: Path) -> None:
    """Models, Image, and Voice destinations carry a route only, never state."""
    packages = _packages(_config(tmp_path))
    openai = packages["openai"]
    assert openai.configuration is not None
    assert (openai.configuration.section, openai.configuration.item) == ("models", "openai")
    sections = {
        component.kind: component.configuration
        for component in openai.components
        if component.configuration is not None
    }
    assert sections[ExtensionComponentKind.LLM_PROVIDER].section == "models"
    assert sections[ExtensionComponentKind.IMAGE_PROVIDER].section == "image"
    assert sections[ExtensionComponentKind.IMAGE_PROVIDER].item is None
    assert sections[ExtensionComponentKind.TRANSCRIPTION_PROVIDER].section == "voice"
    assert sections[ExtensionComponentKind.TRANSCRIPTION_PROVIDER].item is None


def test_llm_and_transcription_components_declare_no_reload(tmp_path: Path) -> None:
    """LLM refreshes on the next turn and transcription resolves per request."""
    packages = _packages(_config(tmp_path))
    for package in packages.values():
        for component in package.components:
            if component.kind is ExtensionComponentKind.LLM_PROVIDER:
                assert component.actions == frozenset(
                    {ExtensionAction.INSPECT, ExtensionAction.CONFIGURE}
                )
                assert "refresh:next-turn" in component.capabilities
            if component.kind is ExtensionComponentKind.TRANSCRIPTION_PROVIDER:
                assert ExtensionAction.RELOAD not in component.actions
                assert "refresh:per-request" in component.capabilities


def test_provider_matching_and_registry_order_are_unchanged_by_projection(
    tmp_path: Path,
) -> None:
    """Projection is read-only: matching order, routing, aliases, and defaults still hold."""
    registry_order = tuple(spec.name for spec in PROVIDERS)
    image_order = image_gen_provider_names()
    transcription_order = transcription_provider_names()

    config = _config(tmp_path)
    config.providers.openai.api_key = "sk-live"
    config.providers.openrouter.api_key = "sk-gateway"
    config.providers.ollama.api_base = "http://127.0.0.1:11434/v1"
    routed = (
        config.get_provider_name("openai/gpt-5.4"),
        config.get_provider_name("openrouter/anything"),
        config.get_provider_name("llama3.2"),
    )

    _adapter(config).snapshot()

    assert tuple(spec.name for spec in PROVIDERS) == registry_order
    assert image_gen_provider_names() == image_order
    assert transcription_provider_names() == transcription_order
    assert (
        config.get_provider_name("openai/gpt-5.4"),
        config.get_provider_name("openrouter/anything"),
        config.get_provider_name("llama3.2"),
    ) == routed
    assert routed == ("openai", "openrouter", "ollama")

    mimo = resolve_transcription_provider("mimo")
    assert mimo is not None and mimo.name == "xiaomi_mimo"
    silicon = resolve_transcription_provider("silicon")
    assert silicon is not None and silicon.name == "siliconflow"
    assert {spec.name: spec.default_model for spec in TRANSCRIPTION_PROVIDERS} == {
        "groq": "whisper-large-v3",
        "openai": "whisper-1",
        "openrouter": "openai/whisper-1",
        "xiaomi_mimo": "mimo-v2.5-asr",
        "stepfun": "stepaudio-2.5-asr",
        "assemblyai": "universal-3-pro,universal-2",
        "siliconflow": "FunAudioLLM/SenseVoiceSmall",
    }


def test_gateway_fallback_resolves_a_keywordless_model_by_registry_order(
    tmp_path: Path,
) -> None:
    """Pin the no-keyword-match fallback branch: gateways win, OAuth is skipped.

    Only explicitly configured providers can be selected here, so adding a provider to
    the registry cannot turn this red; a reorder that demotes gateways can, which is the
    behavior being pinned.
    """
    model = "zzz-unknown-model-9000"

    def configured(**keys: str) -> Config:
        config = _config(tmp_path)
        for name, key in keys.items():
            getattr(config.providers, name).api_key = key
        return config

    gateway_only = configured(openrouter="sk-gateway")
    before = gateway_only.get_provider_name(model)
    _adapter(gateway_only).snapshot()

    assert before == "openrouter"
    assert gateway_only.get_provider_name(model) == before

    # A gateway is preferred over a configured non-gateway that sits later in the registry.
    assert configured(openrouter="sk-gateway", deepseek="sk-direct").get_provider_name(
        model
    ) == "openrouter"
    # Without a gateway the same branch continues in registry order.
    assert configured(deepseek="sk-direct").get_provider_name(model) == "deepseek"
    # OAuth providers are never fallback candidates; they need an explicit model choice.
    assert configured(github_copilot="sk-oauth", deepseek="sk-direct").get_provider_name(
        model
    ) == "deepseek"

    # A configured local endpoint still takes precedence over the gateway loop.
    local_first = configured(openrouter="sk-gateway")
    local_first.providers.ollama.api_base = "http://127.0.0.1:11434/v1"
    assert local_first.get_provider_name(model) == "ollama"


def test_preset_and_fallback_resolution_survive_projection(tmp_path: Path) -> None:
    """Preset and fallback-preset resolution are unaffected by inventory projection."""
    config = Config.model_validate(
        {
            "agents": {"defaults": {"workspace": str(tmp_path), "modelPreset": "primary"}},
            "providers": {"openai": {"apiKey": "sk-live"}},
            "modelPresets": {
                "primary": {"model": "openai/gpt-5.4", "provider": "openai"},
                "backup": {"model": "openrouter/backup", "provider": "openrouter"},
            },
        }
    )
    before = (config.resolve_preset().model, config.resolve_preset("backup").model)

    _adapter(config).snapshot()

    assert (config.resolve_preset().model, config.resolve_preset("backup").model) == before
    assert before == ("openai/gpt-5.4", "openrouter/backup")
