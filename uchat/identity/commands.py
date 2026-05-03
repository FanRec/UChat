from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from uchat.identity.service import IdentityError, IdentityService
from uchat.identity.types import IdentityContext, IdentityResolution, Person, PlatformAccount, VerificationChallenge


@dataclass(frozen=True)
class IssueChallengeCommand:
    platform: str
    platform_user_id: str


@dataclass(frozen=True)
class ConsumeChallengeCommand:
    platform: str
    platform_user_id: str
    platform_nickname: str
    code: str
    binding_evidence: list[dict[str, object]]


@dataclass(frozen=True)
class BindAccountCommand:
    platform: str
    platform_user_id: str
    person_id: str
    resolution: IdentityResolution


@dataclass(frozen=True)
class RenamePersonCommand:
    person_id: str
    display_name: str


def issue_challenge(service: IdentityService, command: IssueChallengeCommand) -> VerificationChallenge:
    return service.issue_verification_challenge(
        platform=command.platform,
        platform_user_id=command.platform_user_id,
    )


def consume_challenge(service: IdentityService, command: ConsumeChallengeCommand) -> IdentityContext:
    return service.consume_verification_challenge(
        platform=command.platform,
        platform_user_id=command.platform_user_id,
        platform_nickname=command.platform_nickname,
        code=command.code,
        binding_evidence=command.binding_evidence,
    )


def bind_account(service: IdentityService, command: BindAccountCommand) -> tuple[PlatformAccount, IdentityContext]:
    account = service.bind_account_to_person(
        platform=command.platform,
        platform_user_id=command.platform_user_id,
        person_id=command.person_id,
        resolution=command.resolution,
    )
    context = service.resolve_account_identity(command.platform, command.platform_user_id)
    return account, context


def rename_person(service: IdentityService, command: RenamePersonCommand) -> Person:
    return service.rename_person(person_id=command.person_id, display_name=command.display_name)


def get_account_identity(service: IdentityService, platform: str, platform_user_id: str) -> tuple[PlatformAccount, IdentityContext]:
    account = service.require_account(platform, platform_user_id)
    context = service.resolve_account_identity(platform, platform_user_id)
    return account, context


def get_person_snapshot(service: IdentityService, person_id: str) -> dict[str, Any]:
    person = service.store.get_person(person_id)
    if person is None:
        raise IdentityError(f"person not found: {person_id}")
    accounts = service.store.list_accounts_for_person(person_id)
    return {
        "person": asdict(person),
        "accounts": [asdict(account) for account in accounts],
    }
