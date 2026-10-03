"""Lookup in master data imported into the platform (CSV import)."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from idp.domain.matching import (
    Match,
    Record,
    normalize_identifier,
    normalize_name,
    score_record,
)
from idp.infrastructure.db.models import Connection, MasterDataRecord

MAX_NAME_CANDIDATES = 200


def to_record(row: MasterDataRecord) -> Record:
    return Record(key=row.record_key, name=row.name, attributes=row.attributes)


class MasterDataProvider:
    kind = "master_data"
    is_mock = False

    async def lookup(
        self,
        session: AsyncSession,
        connection: Connection,
        entity: str,
        criteria: Mapping[str, str],
    ) -> list[Match]:
        conditions = []
        if criteria.get("key"):
            conditions.append(MasterDataRecord.record_key == criteria["key"])
        if criteria.get("tax_id"):
            conditions.append(
                MasterDataRecord.tax_id_norm == normalize_identifier(criteria["tax_id"])
            )
        if criteria.get("iban"):
            conditions.append(MasterDataRecord.iban_norm == normalize_identifier(criteria["iban"]))
        name = normalize_name(criteria.get("name", ""))
        if name:
            # Candidate narrowing by the longest name token; ranking happens in Python.
            token = max(name.split(), key=len)
            conditions.append(MasterDataRecord.name_norm.contains(token, autoescape=True))
        if not conditions:
            return []
        rows = (
            await session.scalars(
                select(MasterDataRecord)
                .where(
                    MasterDataRecord.connection_id == connection.id,
                    MasterDataRecord.entity == entity,
                    or_(*conditions),
                )
                .limit(MAX_NAME_CANDIDATES)
            )
        ).all()
        matches = (score_record(to_record(r), criteria) for r in rows)
        return [m for m in matches if m is not None]
