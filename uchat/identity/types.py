from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


IdentityResolution = Literal["unknown", "probable", "verified"]


@dataclass
class PlatformAccount:
    platform: str
    platform_user_id: str
    platform_nickname: str
    first_seen_at: str
    last_seen_at: str
    binding_evidence: list[dict[str, object]] = field(default_factory=list)
    linked_person_id: str = ""
    identity_resolution: IdentityResolution = "unknown"

    @property
    def account_key(self) -> tuple[str, str]:
        return (self.platform, self.platform_user_id)


@dataclass
class Person:
    person_id: str
    display_name: str
    created_at: str
    updated_at: str


@dataclass
class VerificationChallenge:
    code: str
    source_platform: str
    source_platform_user_id: str
    issued_at: str
    expires_at_epoch_s: float
    consumed: bool = False
    consumed_at: str = ""


@dataclass(frozen=True)
class IdentityContext:
    resolved_person_id: str
    identity_resolution: IdentityResolution
    display_name: str
    platform: str
    platform_label: str
    platform_nickname: str
    addressing_guidance: str

