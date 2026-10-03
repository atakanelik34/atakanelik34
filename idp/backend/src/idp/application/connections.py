"""Connections (enrichment sources) and master-data import.

Configs are validated per kind when saved; REST hosts must be allow-listed.
A CSV import *replaces* the records of one entity in a master-data connection
(columns `key`, `name`, optional `tax_id`, `iban`, any other column becomes an
attribute). Everything is tenant-scoped and audited; record contents are not
written to the audit log, only counts.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.config import Settings
from idp.domain.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
)
from idp.domain.identity import Permission, Principal
from idp.domain.matching import Decision, decide, normalize_identifier, normalize_name
from idp.infrastructure.db.models import Connection, MasterDataRecord
from idp.providers.enrichment.base import CONFIG_MODELS, EnrichmentError, EnrichmentProvider

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
ATTRIBUTE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MAX_VALUE_LENGTH = 500


@dataclass(frozen=True, slots=True)
class ConnectionSummary:
    connection: Connection
    records: int


@dataclass(frozen=True, slots=True)
class ImportReport:
    imported: int
    skipped: int
    errors: list[str]


class ConnectionService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    @staticmethod
    def _require(principal: Principal, permission: Permission) -> None:
        if not principal.has(permission):
            raise AuthorizationError(f"Missing permission '{permission.value}'")

    async def _get(self, principal: Principal, connection_id: uuid.UUID) -> Connection:
        connection = await self._session.get(Connection, connection_id)
        if connection is None or connection.tenant_id != principal.tenant_id:
            raise NotFoundError("Connection not found")
        return connection

    def _validated_config(self, kind: str, config: Mapping[str, Any]) -> dict[str, Any]:
        model = CONFIG_MODELS.get(kind)
        if model is None:
            raise ValidationError(f"Unknown connection kind '{kind}'")
        try:
            parsed = model.model_validate(dict(config))
        except PydanticValidationError as exc:
            raise ValidationError(
                "Connection configuration is invalid",
                details={
                    "errors": [
                        {"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()
                    ]
                },
            ) from exc
        data = parsed.model_dump(exclude_none=True)
        allow_lists = {
            "rest": (
                "base_url",
                self._settings.enrichment_allowed_host_set,
                "ENRICHMENT_ALLOWED_HOSTS",
            ),
            "webhook": ("url", self._settings.webhook_allowed_host_set, "WEBHOOK_ALLOWED_HOSTS"),
        }
        if kind in allow_lists:
            attribute, allowed, setting = allow_lists[kind]
            host = (urlsplit(str(data[attribute])).hostname or "").lower()
            if host not in allowed:
                raise ValidationError(f"Host '{host}' is not in {setting}", details={"host": host})
        return data

    async def _count(self, connection_id: uuid.UUID) -> int:
        return (
            await self._session.scalar(
                select(func.count()).where(MasterDataRecord.connection_id == connection_id)
            )
        ) or 0

    async def list_connections(self, principal: Principal) -> list[ConnectionSummary]:
        self._require(principal, Permission.CONFIG_READ)
        rows = (
            await self._session.scalars(
                select(Connection)
                .where(Connection.tenant_id == principal.tenant_id)
                .order_by(Connection.key)
            )
        ).all()
        return [ConnectionSummary(c, await self._count(c.id)) for c in rows]

    async def get(self, principal: Principal, connection_id: uuid.UUID) -> ConnectionSummary:
        self._require(principal, Permission.CONFIG_READ)
        connection = await self._get(principal, connection_id)
        return ConnectionSummary(connection, await self._count(connection.id))

    async def create(
        self, principal: Principal, *, key: str, name: str, kind: str, config: Mapping[str, Any]
    ) -> ConnectionSummary:
        self._require(principal, Permission.CONFIG_WRITE)
        if not KEY_PATTERN.match(key):
            raise ValidationError("Key must be lowercase letters, digits, '-' or '_'")
        connection = Connection(
            tenant_id=principal.tenant_id,
            key=key,
            name=name,
            kind=kind,
            config=self._validated_config(kind, config),
            created_by_id=principal.user_id,
        )
        self._session.add(connection)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(f"A connection with key '{key}' already exists") from exc
        record_audit(
            self._session,
            action=AuditAction.CONNECTION_CREATED,
            entity_type=AuditEntity.CONNECTION,
            entity_id=connection.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={"key": key, "kind": kind, "config": connection.config},
        )
        await self._session.commit()
        return ConnectionSummary(connection, 0)

    async def update(
        self,
        principal: Principal,
        connection_id: uuid.UUID,
        *,
        name: str | None,
        config: Mapping[str, Any] | None,
        is_active: bool | None,
    ) -> ConnectionSummary:
        self._require(principal, Permission.CONFIG_WRITE)
        connection = await self._get(principal, connection_id)
        before = {
            "name": connection.name,
            "config": connection.config,
            "is_active": connection.is_active,
        }
        if name is not None:
            connection.name = name
        if config is not None:
            connection.config = self._validated_config(connection.kind, config)
        if is_active is not None:
            connection.is_active = is_active
        record_audit(
            self._session,
            action=AuditAction.CONNECTION_UPDATED,
            entity_type=AuditEntity.CONNECTION,
            entity_id=connection.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            before=before,
            after={
                "name": connection.name,
                "config": connection.config,
                "is_active": connection.is_active,
            },
        )
        await self._session.commit()
        return ConnectionSummary(connection, await self._count(connection.id))

    async def delete(self, principal: Principal, connection_id: uuid.UUID) -> None:
        self._require(principal, Permission.CONFIG_WRITE)
        connection = await self._get(principal, connection_id)
        record_audit(
            self._session,
            action=AuditAction.CONNECTION_DELETED,
            entity_type=AuditEntity.CONNECTION,
            entity_id=connection.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            before={"key": connection.key, "kind": connection.kind},
        )
        await self._session.delete(connection)
        await self._session.commit()

    # --- master data -----------------------------------------------------------------

    def _parse_csv(self, data: bytes) -> tuple[list[dict[str, Any]], list[str], int]:
        if len(data) > self._settings.master_data_max_import_bytes:
            raise PayloadTooLargeError("The CSV file is too large")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("The CSV file must be UTF-8 encoded") from exc
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        if "key" not in headers or "name" not in headers:
            raise ValidationError("The CSV needs at least the columns 'key' and 'name'")
        reader.fieldnames = headers
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        skipped = 0
        seen: set[str] = set()
        for line, raw in enumerate(reader, start=2):
            if len(rows) >= self._settings.master_data_max_rows:
                raise ValidationError(
                    f"The CSV has more than {self._settings.master_data_max_rows} rows"
                )
            key = (raw.get("key") or "").strip()[:128]
            name = (raw.get("name") or "").strip()[:300]
            if not key or not name:
                skipped += 1
                if len(errors) < 20:
                    errors.append(f"line {line}: key and name are required")
                continue
            if key in seen:
                skipped += 1
                if len(errors) < 20:
                    errors.append(f"line {line}: duplicate key '{key}'")
                continue
            seen.add(key)
            attributes = {
                column: value.strip()[:MAX_VALUE_LENGTH]
                for column, value in raw.items()
                if column
                and column not in ("key", "name")
                and ATTRIBUTE.match(column)
                and isinstance(value, str)
                and value.strip()
            }
            rows.append({"key": key, "name": name, "attributes": attributes})
        return rows, errors, skipped

    async def import_csv(
        self, principal: Principal, connection_id: uuid.UUID, *, entity: str, data: bytes
    ) -> ImportReport:
        self._require(principal, Permission.CONFIG_WRITE)
        connection = await self._get(principal, connection_id)
        if connection.kind != "master_data":
            raise ValidationError("Records can only be imported into master-data connections")
        if not ATTRIBUTE.match(entity):
            raise ValidationError("Invalid entity name")
        rows, errors, skipped = self._parse_csv(data)
        await self._session.execute(
            delete(MasterDataRecord).where(
                MasterDataRecord.connection_id == connection.id, MasterDataRecord.entity == entity
            )
        )
        if rows:
            await self._session.execute(
                insert(MasterDataRecord),
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": principal.tenant_id,
                        "connection_id": connection.id,
                        "entity": entity,
                        "record_key": r["key"],
                        "name": r["name"],
                        "name_norm": normalize_name(r["name"]),
                        "tax_id_norm": normalize_identifier(r["attributes"]["tax_id"])
                        if r["attributes"].get("tax_id")
                        else None,
                        "iban_norm": normalize_identifier(r["attributes"]["iban"])
                        if r["attributes"].get("iban")
                        else None,
                        "attributes": r["attributes"],
                    }
                    for r in rows
                ],
            )
        record_audit(
            self._session,
            action=AuditAction.MASTER_DATA_IMPORTED,
            entity_type=AuditEntity.CONNECTION,
            entity_id=connection.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={"entity": entity, "imported": len(rows), "skipped": skipped},
        )
        await self._session.commit()
        return ImportReport(imported=len(rows), skipped=skipped, errors=errors)

    async def records(
        self,
        principal: Principal,
        connection_id: uuid.UUID,
        *,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[MasterDataRecord], int]:
        self._require(principal, Permission.CONFIG_READ)
        connection = await self._get(principal, connection_id)
        conditions = [MasterDataRecord.connection_id == connection.id]
        if query and (needle := normalize_name(query)):
            conditions.append(MasterDataRecord.name_norm.contains(needle, autoescape=True))
        total = await self._session.scalar(select(func.count()).where(*conditions)) or 0
        rows = (
            await self._session.scalars(
                select(MasterDataRecord)
                .where(*conditions)
                .order_by(MasterDataRecord.name)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return rows, total

    async def test_lookup(
        self,
        principal: Principal,
        connection_id: uuid.UUID,
        *,
        entity: str,
        criteria: Mapping[str, str],
        providers: Mapping[str, EnrichmentProvider],
        min_score: float = 0.85,
    ) -> Decision:
        self._require(principal, Permission.CONFIG_WRITE)
        connection = await self._get(principal, connection_id)
        provider = providers.get(connection.kind)
        if provider is None:
            raise ValidationError(f"No provider for connection kind '{connection.kind}'")
        if provider.is_mock and not self._settings.allow_mock_providers:
            raise ValidationError("Mock providers are disabled in this deployment")
        try:
            matches = await provider.lookup(self._session, connection, entity, criteria)
        except EnrichmentError as exc:
            raise ValidationError(
                f"Lookup failed: {exc.code}", details={"error": exc.code}
            ) from exc
        return decide(matches, min_score)
