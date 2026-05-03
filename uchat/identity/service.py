from __future__ import annotations

import secrets
import time
from dataclasses import asdict
from uuid import uuid4

from uchat.contracts import NormalizedEvent, utc_now_iso
from uchat.identity.store import IdentityStore, InMemoryIdentityStore
from uchat.identity.types import IdentityContext, IdentityResolution, Person, PlatformAccount, VerificationChallenge


_PLATFORM_LABELS = {
    "bilibili": "B站直播",
    "console": "控制台",
    "local_asr": "本地语音",
}


class IdentityError(RuntimeError):
    pass


class IdentityService:
    def __init__(
        self,
        *,
        store: IdentityStore | None = None,
        challenge_ttl_seconds: int = 600,
        default_console_person_id: str = "",
    ) -> None:
        self.store = store or InMemoryIdentityStore()
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self.default_console_person_id = default_console_person_id

    def resolve_event_identity(self, event: NormalizedEvent) -> IdentityContext:
        platform = str(event.metadata.get("platform", "")).strip()
        platform_user_id = self._platform_user_id_for_event(event)
        platform_nickname = self._platform_nickname_for_event(event)

        if platform and platform_user_id:
            account = self.observe_account(
                platform=platform,
                platform_user_id=platform_user_id,
                platform_nickname=platform_nickname,
                binding_evidence=list(event.binding_evidence),
            )
            if platform == "console" and self.default_console_person_id and not account.linked_person_id:
                try:
                    account = self.bind_account_to_person(
                        platform=platform,
                        platform_user_id=platform_user_id,
                        person_id=self.default_console_person_id,
                        resolution="verified",
                    )
                except IdentityError:
                    pass
            context = self.resolve_account_identity(account.platform, account.platform_user_id)
            event.resolved_person_id = context.resolved_person_id
            event.identity_resolution = context.identity_resolution
            event.metadata["identity_context"] = asdict(context)
            return context

        context = IdentityContext(
            resolved_person_id="",
            identity_resolution=event.identity_resolution if event.identity_resolution in {"unknown", "probable", "verified"} else "unknown",
            display_name="",
            platform=platform,
            platform_label=self.platform_label(platform),
            platform_nickname=platform_nickname,
            addressing_guidance=self._addressing_guidance(
                resolution="unknown",
                display_name="",
                platform_nickname=platform_nickname,
            ),
        )
        event.metadata["identity_context"] = asdict(context)
        return context

    def resolve_account_identity(self, platform: str, platform_user_id: str) -> IdentityContext:
        account = self.require_account(platform, platform_user_id)
        person = self.store.get_person(account.linked_person_id) if account.linked_person_id else None
        display_name = person.display_name if person is not None else ""
        resolution = account.identity_resolution if account.identity_resolution in {"unknown", "probable", "verified"} else "unknown"
        return IdentityContext(
            resolved_person_id=account.linked_person_id,
            identity_resolution=resolution,
            display_name=display_name,
            platform=platform,
            platform_label=self.platform_label(platform),
            platform_nickname=account.platform_nickname,
            addressing_guidance=self._addressing_guidance(
                resolution=resolution,
                display_name=display_name,
                platform_nickname=account.platform_nickname,
            ),
        )

    def observe_account(
        self,
        *,
        platform: str,
        platform_user_id: str,
        platform_nickname: str,
        binding_evidence: list[dict[str, object]] | None = None,
        seen_at: str | None = None,
    ) -> PlatformAccount:
        timestamp = seen_at or utc_now_iso()
        existing = self.store.get_account(platform, platform_user_id)
        if existing is None:
            account = PlatformAccount(
                platform=platform,
                platform_user_id=platform_user_id,
                platform_nickname=platform_nickname,
                first_seen_at=timestamp,
                last_seen_at=timestamp,
                binding_evidence=self._dedupe_binding_evidence(binding_evidence or []),
                linked_person_id="",
                identity_resolution="unknown",
            )
            return self.store.save_account(account)

        merged_evidence = self._merge_binding_evidence(existing.binding_evidence, binding_evidence or [])
        existing.platform_nickname = platform_nickname or existing.platform_nickname
        existing.last_seen_at = timestamp
        existing.binding_evidence = merged_evidence
        return self.store.save_account(existing)

    def create_person_for_account(
        self,
        *,
        platform: str,
        platform_user_id: str,
        display_name: str = "",
        resolution: IdentityResolution = "probable",
    ) -> Person:
        account = self.require_account(platform, platform_user_id)
        if account.linked_person_id:
            person = self.store.get_person(account.linked_person_id)
            if person is None:
                raise IdentityError(f"linked person missing: {account.linked_person_id}")
            return person
        timestamp = utc_now_iso()
        person = Person(
            person_id=f"person_{uuid4().hex[:16]}",
            display_name=display_name or account.platform_nickname,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.store.save_person(person)
        self.bind_account_to_person(
            platform=platform,
            platform_user_id=platform_user_id,
            person_id=person.person_id,
            resolution=resolution,
        )
        return person

    def bind_account_to_person(
        self,
        *,
        platform: str,
        platform_user_id: str,
        person_id: str,
        resolution: IdentityResolution = "probable",
    ) -> PlatformAccount:
        account = self.require_account(platform, platform_user_id)
        if self.store.get_person(person_id) is None:
            raise IdentityError(f"person not found: {person_id}")
        account.linked_person_id = person_id
        account.identity_resolution = resolution
        return self.store.save_account(account)

    def rename_person(self, *, person_id: str, display_name: str) -> Person:
        person = self.store.get_person(person_id)
        if person is None:
            raise IdentityError(f"person not found: {person_id}")
        person.display_name = display_name
        person.updated_at = utc_now_iso()
        return self.store.save_person(person)

    def issue_verification_challenge(self, *, platform: str, platform_user_id: str) -> VerificationChallenge:
        self.require_account(platform, platform_user_id)
        challenge = VerificationChallenge(
            code=f"{secrets.randbelow(1_000_000):06d}",
            source_platform=platform,
            source_platform_user_id=platform_user_id,
            issued_at=utc_now_iso(),
            expires_at_epoch_s=time.time() + self.challenge_ttl_seconds,
        )
        return self.store.save_challenge(challenge)

    def consume_verification_challenge(
        self,
        *,
        platform: str,
        platform_user_id: str,
        platform_nickname: str,
        code: str,
        binding_evidence: list[dict[str, object]] | None = None,
    ) -> IdentityContext:
        challenge = self.store.get_challenge(code)
        if challenge is None:
            raise IdentityError("verification challenge not found")
        if challenge.consumed:
            raise IdentityError("verification challenge already consumed")
        if time.time() > challenge.expires_at_epoch_s:
            raise IdentityError("verification challenge expired")

        source_account = self.require_account(challenge.source_platform, challenge.source_platform_user_id)
        target_account = self.observe_account(
            platform=platform,
            platform_user_id=platform_user_id,
            platform_nickname=platform_nickname,
            binding_evidence=binding_evidence or [],
        )
        if source_account.linked_person_id:
            person = self.store.get_person(source_account.linked_person_id)
            if person is None:
                raise IdentityError(f"linked person missing: {source_account.linked_person_id}")
        else:
            person = self.create_person_for_account(
                platform=source_account.platform,
                platform_user_id=source_account.platform_user_id,
                resolution="verified",
            )
        self.bind_account_to_person(
            platform=source_account.platform,
            platform_user_id=source_account.platform_user_id,
            person_id=person.person_id,
            resolution="verified",
        )
        self.bind_account_to_person(
            platform=target_account.platform,
            platform_user_id=target_account.platform_user_id,
            person_id=person.person_id,
            resolution="verified",
        )
        challenge.consumed = True
        challenge.consumed_at = utc_now_iso()
        self.store.save_challenge(challenge)
        return self.resolve_account_identity(platform, platform_user_id)

    def render_identity_prompt_context(self, context: IdentityContext) -> str:
        return "\n".join(
            [
                f"identity_resolution: {context.identity_resolution}",
                f"resolved_person_id: {context.resolved_person_id}",
                f"display_name: {context.display_name}",
                f"platform: {context.platform}",
                f"platform_label: {context.platform_label}",
                f"platform_nickname: {context.platform_nickname}",
                f"addressing_guidance: {context.addressing_guidance}",
            ]
        )

    def require_account(self, platform: str, platform_user_id: str) -> PlatformAccount:
        account = self.store.get_account(platform, platform_user_id)
        if account is None:
            raise IdentityError(f"account not found: {platform}:{platform_user_id}")
        return account

    def close(self) -> None:
        self.store.close()

    def platform_label(self, platform: str) -> str:
        return _PLATFORM_LABELS.get(platform, platform or "unknown")

    def _platform_user_id_for_event(self, event: NormalizedEvent) -> str:
        platform_user_id = str(event.metadata.get("platform_user_id", "")).strip()
        if platform_user_id:
            return platform_user_id
        platform = str(event.metadata.get("platform", "")).strip()
        if platform == "console":
            return str(event.metadata.get("console_user_id", "")).strip() or "user_local_default"
        if platform == "local_asr":
            speaker_id = str(event.metadata.get("speaker_id", "")).strip()
            if speaker_id:
                return speaker_id
        for candidate in event.speaker_candidates:
            entity_id = str(candidate.get("entity_id", "")).strip()
            if ":" in entity_id:
                _, _, right = entity_id.partition(":")
                if right:
                    return right
            if entity_id and str(candidate.get("speaker_role", "")) in {"user", "viewer"}:
                return entity_id
        return ""

    def _platform_nickname_for_event(self, event: NormalizedEvent) -> str:
        nickname = str(event.metadata.get("username", "")).strip()
        if nickname:
            return nickname
        platform = str(event.metadata.get("platform", "")).strip()
        if platform == "console":
            return str(event.metadata.get("console_display_name", "")).strip() or "控制台用户"
        if platform == "local_asr":
            return str(event.metadata.get("speaker_display_name", "")).strip() or str(event.metadata.get("speaker_id", "")).strip()
        return ""

    def _addressing_guidance(
        self,
        *,
        resolution: IdentityResolution,
        display_name: str,
        platform_nickname: str,
    ) -> str:
        if resolution == "verified" and display_name:
            if platform_nickname and platform_nickname != display_name:
                return f"优先使用 display_name “{display_name}” 称呼；如需贴近平台现场，可参考平台昵称“{platform_nickname}”。"
            return f"优先使用 display_name “{display_name}” 称呼。"
        if resolution == "probable" and display_name:
            if platform_nickname and platform_nickname != display_name:
                return f"当前身份尚未强验证，优先尝试使用 display_name “{display_name}”；若不自然，再回退平台昵称“{platform_nickname}”。"
            return f"当前身份尚未强验证，可优先尝试使用 display_name “{display_name}”。"
        if platform_nickname:
            return f"当前没有稳定身份，默认回退使用平台昵称“{platform_nickname}”，不要把它当作长期真名。"
        return "当前没有稳定身份，使用泛称呼，不要把平台信息当作长期身份。"

    def _dedupe_binding_evidence(self, evidence: list[dict[str, object]]) -> list[dict[str, object]]:
        unique: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            key = (str(item.get("evidence_type", "")), str(item.get("value", "")))
            if key in seen:
                continue
            seen.add(key)
            unique.append(dict(item))
        return unique

    def _merge_binding_evidence(
        self,
        existing: list[dict[str, object]],
        incoming: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return self._dedupe_binding_evidence([*existing, *incoming])
