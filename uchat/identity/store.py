from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from uchat.identity.types import Person, PlatformAccount, VerificationChallenge


class IdentityStore(Protocol):
    def get_account(self, platform: str, platform_user_id: str) -> PlatformAccount | None: ...

    def save_account(self, account: PlatformAccount) -> PlatformAccount: ...

    def list_accounts_for_person(self, person_id: str) -> list[PlatformAccount]: ...

    def get_person(self, person_id: str) -> Person | None: ...

    def save_person(self, person: Person) -> Person: ...

    def get_challenge(self, code: str) -> VerificationChallenge | None: ...

    def save_challenge(self, challenge: VerificationChallenge) -> VerificationChallenge: ...

    def close(self) -> None: ...


class InMemoryIdentityStore:
    def __init__(self) -> None:
        self._accounts: dict[tuple[str, str], PlatformAccount] = {}
        self._persons: dict[str, Person] = {}
        self._challenges: dict[str, VerificationChallenge] = {}

    def get_account(self, platform: str, platform_user_id: str) -> PlatformAccount | None:
        account = self._accounts.get((platform, platform_user_id))
        return replace(account) if account is not None else None

    def save_account(self, account: PlatformAccount) -> PlatformAccount:
        self._accounts[account.account_key] = replace(account)
        return replace(account)

    def list_accounts_for_person(self, person_id: str) -> list[PlatformAccount]:
        return [replace(account) for account in self._accounts.values() if account.linked_person_id == person_id]

    def get_person(self, person_id: str) -> Person | None:
        person = self._persons.get(person_id)
        return replace(person) if person is not None else None

    def save_person(self, person: Person) -> Person:
        self._persons[person.person_id] = replace(person)
        return replace(person)

    def get_challenge(self, code: str) -> VerificationChallenge | None:
        challenge = self._challenges.get(code)
        return replace(challenge) if challenge is not None else None

    def save_challenge(self, challenge: VerificationChallenge) -> VerificationChallenge:
        self._challenges[challenge.code] = replace(challenge)
        return replace(challenge)

    def close(self) -> None:
        return None


class SQLiteIdentityStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def get_account(self, platform: str, platform_user_id: str) -> PlatformAccount | None:
        row = self._conn.execute(
            """
            SELECT platform, platform_user_id, platform_nickname, first_seen_at, last_seen_at,
                   binding_evidence_json, linked_person_id, identity_resolution
            FROM platform_accounts
            WHERE platform = ? AND platform_user_id = ?
            """,
            (platform, platform_user_id),
        ).fetchone()
        return self._row_to_account(row) if row is not None else None

    def save_account(self, account: PlatformAccount) -> PlatformAccount:
        self._conn.execute(
            """
            INSERT INTO platform_accounts (
                platform, platform_user_id, platform_nickname, first_seen_at, last_seen_at,
                binding_evidence_json, linked_person_id, identity_resolution
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, platform_user_id) DO UPDATE SET
                platform_nickname = excluded.platform_nickname,
                first_seen_at = excluded.first_seen_at,
                last_seen_at = excluded.last_seen_at,
                binding_evidence_json = excluded.binding_evidence_json,
                linked_person_id = excluded.linked_person_id,
                identity_resolution = excluded.identity_resolution
            """,
            (
                account.platform,
                account.platform_user_id,
                account.platform_nickname,
                account.first_seen_at,
                account.last_seen_at,
                json.dumps(account.binding_evidence, ensure_ascii=False),
                account.linked_person_id,
                account.identity_resolution,
            ),
        )
        self._replace_binding_evidence(account)
        self._conn.commit()
        stored = self.get_account(account.platform, account.platform_user_id)
        assert stored is not None
        return stored

    def list_accounts_for_person(self, person_id: str) -> list[PlatformAccount]:
        rows = self._conn.execute(
            """
            SELECT platform, platform_user_id, platform_nickname, first_seen_at, last_seen_at,
                   binding_evidence_json, linked_person_id, identity_resolution
            FROM platform_accounts
            WHERE linked_person_id = ?
            ORDER BY platform, platform_user_id
            """,
            (person_id,),
        ).fetchall()
        return [self._row_to_account(row) for row in rows]

    def get_person(self, person_id: str) -> Person | None:
        row = self._conn.execute(
            """
            SELECT person_id, display_name, created_at, updated_at
            FROM persons
            WHERE person_id = ?
            """,
            (person_id,),
        ).fetchone()
        return self._row_to_person(row) if row is not None else None

    def save_person(self, person: Person) -> Person:
        self._conn.execute(
            """
            INSERT INTO persons (person_id, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                display_name = excluded.display_name,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (person.person_id, person.display_name, person.created_at, person.updated_at),
        )
        self._conn.commit()
        stored = self.get_person(person.person_id)
        assert stored is not None
        return stored

    def get_challenge(self, code: str) -> VerificationChallenge | None:
        row = self._conn.execute(
            """
            SELECT code, source_platform, source_platform_user_id, issued_at, expires_at_epoch_s,
                   consumed, consumed_at
            FROM verification_challenges
            WHERE code = ?
            """,
            (code,),
        ).fetchone()
        return self._row_to_challenge(row) if row is not None else None

    def save_challenge(self, challenge: VerificationChallenge) -> VerificationChallenge:
        self._conn.execute(
            """
            INSERT INTO verification_challenges (
                code, source_platform, source_platform_user_id, issued_at, expires_at_epoch_s, consumed, consumed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                source_platform = excluded.source_platform,
                source_platform_user_id = excluded.source_platform_user_id,
                issued_at = excluded.issued_at,
                expires_at_epoch_s = excluded.expires_at_epoch_s,
                consumed = excluded.consumed,
                consumed_at = excluded.consumed_at
            """,
            (
                challenge.code,
                challenge.source_platform,
                challenge.source_platform_user_id,
                challenge.issued_at,
                challenge.expires_at_epoch_s,
                1 if challenge.consumed else 0,
                challenge.consumed_at,
            ),
        )
        self._conn.commit()
        stored = self.get_challenge(challenge.code)
        assert stored is not None
        return stored

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS persons (
                person_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_accounts (
                platform TEXT NOT NULL,
                platform_user_id TEXT NOT NULL,
                platform_nickname TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                binding_evidence_json TEXT NOT NULL,
                linked_person_id TEXT NOT NULL,
                identity_resolution TEXT NOT NULL,
                PRIMARY KEY (platform, platform_user_id)
            );

            CREATE TABLE IF NOT EXISTS verification_challenges (
                code TEXT PRIMARY KEY,
                source_platform TEXT NOT NULL,
                source_platform_user_id TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                expires_at_epoch_s REAL NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0,
                consumed_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS account_binding_evidence (
                platform TEXT NOT NULL,
                platform_user_id TEXT NOT NULL,
                evidence_index INTEGER NOT NULL,
                evidence_type TEXT NOT NULL,
                evidence_value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL,
                PRIMARY KEY (platform, platform_user_id, evidence_index)
            );
            """
        )
        self._conn.commit()

    def _replace_binding_evidence(self, account: PlatformAccount) -> None:
        self._conn.execute(
            "DELETE FROM account_binding_evidence WHERE platform = ? AND platform_user_id = ?",
            (account.platform, account.platform_user_id),
        )
        for index, evidence in enumerate(account.binding_evidence):
            self._conn.execute(
                """
                INSERT INTO account_binding_evidence (
                    platform, platform_user_id, evidence_index, evidence_type, evidence_value,
                    confidence, source, observed_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account.platform,
                    account.platform_user_id,
                    index,
                    str(evidence.get("evidence_type", "")),
                    str(evidence.get("value", "")),
                    float(evidence.get("confidence", 0.0) or 0.0),
                    str(evidence.get("source", "")),
                    str(evidence.get("observed_at", "")),
                    json.dumps(evidence, ensure_ascii=False),
                ),
            )

    def _row_to_account(self, row: sqlite3.Row) -> PlatformAccount:
        return PlatformAccount(
            platform=str(row["platform"]),
            platform_user_id=str(row["platform_user_id"]),
            platform_nickname=str(row["platform_nickname"]),
            first_seen_at=str(row["first_seen_at"]),
            last_seen_at=str(row["last_seen_at"]),
            binding_evidence=list(json.loads(str(row["binding_evidence_json"]))),
            linked_person_id=str(row["linked_person_id"]),
            identity_resolution=str(row["identity_resolution"]),  # type: ignore[arg-type]
        )

    def _row_to_person(self, row: sqlite3.Row) -> Person:
        return Person(
            person_id=str(row["person_id"]),
            display_name=str(row["display_name"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _row_to_challenge(self, row: sqlite3.Row) -> VerificationChallenge:
        return VerificationChallenge(
            code=str(row["code"]),
            source_platform=str(row["source_platform"]),
            source_platform_user_id=str(row["source_platform_user_id"]),
            issued_at=str(row["issued_at"]),
            expires_at_epoch_s=float(row["expires_at_epoch_s"]),
            consumed=bool(row["consumed"]),
            consumed_at=str(row["consumed_at"]),
        )
