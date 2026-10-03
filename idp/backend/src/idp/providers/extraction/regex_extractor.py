"""Regex extraction from schema-provided patterns (user regexes, timeout-guarded)."""

from __future__ import annotations

from idp.domain.taxonomy import REGEX_TIMEOUT_SECONDS, FieldType, compile_user_regex
from idp.providers.extraction.base import (
    SCALAR_ROW,
    ExtractionContext,
    FieldCandidate,
    ProviderInfo,
    Suitability,
)
from idp.providers.extraction.common import candidate
from idp.providers.extraction.text_index import span


class RegexExtractor:
    info = ProviderInfo(name="regex-extractor", version="1", method="regex")

    def assess(self, ctx: ExtractionContext) -> Suitability:
        patterns = sum(len(f.extraction_hints.patterns) for _, f in ctx.schema.flatten())
        return Suitability(
            can_handle=patterns > 0,
            expected_confidence=0.9 if patterns else 0.0,
            estimated_cost=0.0,
            reasons=(f"{patterns} schema patterns",),
        )

    async def extract(self, ctx: ExtractionContext) -> list[FieldCandidate]:
        out: list[FieldCandidate] = []
        lines = ctx.lines()
        for path, field in ctx.schema.flatten():
            if "[]" in path or field.type in (FieldType.ARRAY, FieldType.OBJECT):
                continue
            for pattern in field.extraction_hints.patterns:
                compiled = compile_user_regex(pattern)
                for layout, line in lines:
                    try:
                        match = compiled.search(line.text, timeout=REGEX_TIMEOUT_SECONDS)
                    except TimeoutError:
                        continue  # pathological pattern/text: skip rather than hang
                    if match is None:
                        continue
                    group = "value" if "value" in compiled.groupindex else 1
                    start, end = match.span(group)
                    if start < 0:
                        continue
                    out.append(
                        candidate(
                            path=path,
                            row_id=SCALAR_ROW,
                            field=field,
                            span=span(line, start, end),
                            layout=layout,
                            method_key="regex",
                            info=self.info,
                        )
                    )
        return out
