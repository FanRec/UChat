from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping


ProtocolType = Literal["openai_compatible"]
ModelRole = Literal["replyer", "planner", "timing_gate", "safety", "summarizer"]


class ModelConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelProviderConfig:
    provider_id: str
    base_url: str
    api_key_env: str
    api_key: str
    timeout_seconds: float
    protocol: ProtocolType = "openai_compatible"


@dataclass(frozen=True)
class ModelProfileConfig:
    profile_id: str
    provider_id: str
    model: str
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class ModelRouteConfig:
    role: ModelRole
    profile_id: str
    fallback_profile_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelsConfig:
    providers: dict[str, ModelProviderConfig] = field(default_factory=dict)
    profiles: dict[str, ModelProfileConfig] = field(default_factory=dict)
    routes: dict[str, ModelRouteConfig] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        *,
        path: str | Path = "config/models.toml",
        env: Mapping[str, str] | None = None,
    ) -> "ModelsConfig":
        env_values = env or os.environ
        config_path = Path(path)
        if not config_path.exists():
            raise ModelConfigError(f"models config file not found: {config_path}")
        with config_path.open("rb") as f:
            raw = tomllib.load(f)

        providers_raw = _section(raw, "providers", config_path)
        profiles_raw = _section(raw, "profiles", config_path)
        routes_raw = _section(raw, "routes", config_path)

        providers: dict[str, ModelProviderConfig] = {}
        for provider_id, provider_section in providers_raw.items():
            if not isinstance(provider_section, Mapping):
                raise ModelConfigError(f"invalid provider section '{provider_id}' in {config_path}")
            api_key_env = _str(provider_section, "api_key_env", config_path)
            providers[str(provider_id)] = ModelProviderConfig(
                provider_id=str(provider_id),
                base_url=_str(provider_section, "base_url", config_path).rstrip("/"),
                api_key_env=api_key_env,
                api_key=env_values.get(api_key_env, "").strip(),
                timeout_seconds=_float(provider_section, "timeout_seconds", config_path),
                protocol=_protocol(provider_section, "protocol", config_path),
            )

        profiles: dict[str, ModelProfileConfig] = {}
        for profile_id, profile_section in profiles_raw.items():
            if not isinstance(profile_section, Mapping):
                raise ModelConfigError(f"invalid profile section '{profile_id}' in {config_path}")
            provider_id = _str(profile_section, "provider_id", config_path)
            if provider_id not in providers:
                raise ModelConfigError(f"profile '{profile_id}' references unknown provider '{provider_id}'")
            profiles[str(profile_id)] = ModelProfileConfig(
                profile_id=str(profile_id),
                provider_id=provider_id,
                model=_str(profile_section, "model", config_path),
                temperature=_float(profile_section, "temperature", config_path),
                max_tokens=_int(profile_section, "max_tokens", config_path),
            )

        routes: dict[str, ModelRouteConfig] = {}
        for role_name, route_section in routes_raw.items():
            if not isinstance(route_section, Mapping):
                raise ModelConfigError(f"invalid route section '{role_name}' in {config_path}")
            role = _role(role_name, config_path)
            profile_id = _str(route_section, "profile_id", config_path)
            if profile_id not in profiles:
                raise ModelConfigError(f"route '{role}' references unknown profile '{profile_id}'")
            fallback_profile_ids = _string_tuple(route_section.get("fallback_profile_ids", ()), config_path, role)
            for fallback_profile_id in fallback_profile_ids:
                if fallback_profile_id not in profiles:
                    raise ModelConfigError(f"route '{role}' references unknown fallback profile '{fallback_profile_id}'")
            routes[str(role)] = ModelRouteConfig(
                role=role,
                profile_id=profile_id,
                fallback_profile_ids=fallback_profile_ids,
            )

        for required_role in ("replyer", "planner", "timing_gate", "safety", "summarizer"):
            if required_role not in routes:
                raise ModelConfigError(f"missing required route '{required_role}' in {config_path}")

        return cls(providers=providers, profiles=profiles, routes=routes)


@dataclass(frozen=True)
class ResolvedModelRoute:
    role: ModelRole
    provider: ModelProviderConfig
    profile: ModelProfileConfig

    @property
    def route_label(self) -> str:
        return f"{self.role}:{self.provider.provider_id}/{self.profile.model}"


class ModelRouter:
    def __init__(self, config: ModelsConfig):
        from uchat.models.openai_compatible import OpenAICompatibleClient

        self.config = config
        self._openai_client_cls = OpenAICompatibleClient
        self._clients: dict[tuple[str, str], OpenAICompatibleClient] = {}

    def resolve(self, role: ModelRole) -> ResolvedModelRoute:
        route = self.config.routes.get(role)
        if route is None:
            raise ModelConfigError(f"no model route configured for role '{role}'")
        profile = self.config.profiles.get(route.profile_id)
        if profile is None:
            raise ModelConfigError(f"route '{role}' points to missing profile '{route.profile_id}'")
        provider = self.config.providers.get(profile.provider_id)
        if provider is None:
            raise ModelConfigError(f"profile '{profile.profile_id}' points to missing provider '{profile.provider_id}'")
        return ResolvedModelRoute(role=role, provider=provider, profile=profile)

    def client_for_role(self, role: ModelRole):
        resolved = self.resolve(role)
        key = (resolved.provider.provider_id, resolved.profile.profile_id)
        if key not in self._clients:
            if resolved.provider.protocol != "openai_compatible":
                raise ModelConfigError(f"unsupported provider protocol '{resolved.provider.protocol}'")
            self._clients[key] = self._openai_client_cls(resolved.provider, resolved.profile)
        return self._clients[key]

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


def _section(data: Mapping[str, object], key: str, path: Path) -> Mapping[str, object]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ModelConfigError(f"missing [{key}] section in {path}")
    return value


def _str(data: Mapping[str, object], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModelConfigError(f"missing string key '{key}' in {path}")
    return value


def _float(data: Mapping[str, object], key: str, path: Path) -> float:
    value = data.get(key)
    if not isinstance(value, int | float):
        raise ModelConfigError(f"missing number key '{key}' in {path}")
    return float(value)


def _int(data: Mapping[str, object], key: str, path: Path) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise ModelConfigError(f"missing integer key '{key}' in {path}")
    return value


def _protocol(data: Mapping[str, object], key: str, path: Path) -> ProtocolType:
    value = str(data.get(key, "openai_compatible")).strip()
    if value != "openai_compatible":
        raise ModelConfigError(f"unsupported protocol '{value}' in {path}")
    return value  # type: ignore[return-value]


def _role(value: str, path: Path) -> ModelRole:
    role = value.strip()
    if role not in {"replyer", "planner", "timing_gate", "safety", "summarizer"}:
        raise ModelConfigError(f"invalid route role '{value}' in {path}")
    return role  # type: ignore[return-value]


def _string_tuple(value: object, path: Path, role: str) -> tuple[str, ...]:
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    if value in (None, ""):
        return ()
    raise ModelConfigError(f"invalid fallback_profile_ids for route '{role}' in {path}")
