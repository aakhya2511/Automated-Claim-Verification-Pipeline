"""JSONL-backed reference repository with in-memory indices.

Design notes
------------
The catalog is loaded once at startup and indexed into dictionaries and an
inverted token index. Retrieval is therefore a hash lookup or a bounded set
intersection — microseconds, not a network round trip. That is deliberate: the
per-claim latency budget is dominated by whether an LLM call happens, so every
non-LLM stage is engineered to be effectively free.

The class satisfies :class:`app.domain.interfaces.ReferenceRepository`, whose
methods are async. In this implementation nothing awaits, which looks redundant
in isolation; the point is that callers are already written against an
awaitable contract, so substituting a Postgres/asyncpg repository requires no
change above this layer.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.core.exceptions import RepositoryError
from app.core.logging import get_logger
from app.domain.enums import EntityType
from app.domain.models import ReferenceRecord
from app.normalization import text as textutil

logger = get_logger(__name__)

#: Tokens matching more than this fraction of the catalog carry no
#: discriminating power (e.g. "plan" across every plan record) and are skipped
#: during candidate generation to keep intersections small.
_MAX_TOKEN_DOC_RATIO = 0.30


class InMemoryReferenceRepository:
    """Reference repository over a pre-indexed list of records."""

    def __init__(self, records: list[ReferenceRecord]) -> None:
        self._records: dict[str, ReferenceRecord] = {}
        self._by_sku: dict[str, str] = {}
        self._by_product_id: dict[str, str] = {}
        self._by_name: dict[str, str] = {}
        self._postings: dict[str, set[str]] = defaultdict(set)
        self._doc_tokens: dict[str, frozenset[str]] = {}
        self._healthy = False

        self._index(records)
        self._healthy = True

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def from_jsonl(cls, path: str | Path) -> InMemoryReferenceRepository:
        """Load a catalog from JSONL, failing loudly on a malformed line.

        A corrupt reference store is a configuration failure, not a per-request
        error: silently skipping bad rows would let the service answer claims
        against a partially-loaded source of truth.
        """
        catalog_path = Path(path)
        if not catalog_path.is_file():
            raise RepositoryError(
                f"reference catalog not found: {catalog_path}",
                details={"path": str(catalog_path)},
            )

        records: list[ReferenceRecord] = []
        try:
            with catalog_path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        payload = json.loads(stripped)
                        records.append(ReferenceRecord.model_validate(payload))
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise RepositoryError(
                            f"malformed reference record at {catalog_path}:{line_number}",
                            details={"path": str(catalog_path), "line": line_number},
                        ) from exc
        except OSError as exc:
            raise RepositoryError(
                f"cannot read reference catalog: {catalog_path}",
                details={"path": str(catalog_path)},
            ) from exc

        if not records:
            raise RepositoryError(
                f"reference catalog is empty: {catalog_path}",
                details={"path": str(catalog_path)},
            )

        logger.info(
            "reference_catalog_loaded",
            path=str(catalog_path),
            records=len(records),
        )
        return cls(records)

    def _index(self, records: list[ReferenceRecord]) -> None:
        # Offers are indexed last and never overwrite an existing identifier.
        # An offer denormalizes its parent product's SKU and name for evidence
        # display, so indexing them in file order would let "SKU-123" or
        # "Noise Cancelling Headphones" resolve to a promotion instead of the
        # product it promotes. Sellable items own their identifiers; offers are
        # addressable only by their own code and name.
        ordered = sorted(
            records,
            key=lambda record: (record.entity_type is EntityType.OFFER, record.record_id),
        )

        for record in ordered:
            if record.record_id in self._records:
                raise RepositoryError(
                    f"duplicate record_id in reference catalog: {record.record_id}",
                    details={"record_id": record.record_id},
                )
            self._records[record.record_id] = record

            # Identifier indices are squashed so "SKU-1234"/"sku 1234" agree.
            self._by_product_id.setdefault(textutil.squash(record.record_id), record.record_id)
            if record.entity_type is not EntityType.OFFER:
                if record.sku:
                    self._by_sku.setdefault(textutil.squash(record.sku), record.record_id)
                if record.product_id:
                    self._by_product_id.setdefault(
                        textutil.squash(record.product_id), record.record_id
                    )

            names = self._surface_forms(record)
            for name in names:
                # First writer wins: canonical names are added before aliases,
                # so an alias shared by two records cannot shadow a real name.
                self._by_name.setdefault(textutil.fold(name), record.record_id)

            tokens = frozenset(
                token for name in names for token in textutil.tokenize(name, drop_stopwords=True)
            )
            self._doc_tokens[record.record_id] = tokens
            for token in tokens:
                self._postings[token].add(record.record_id)

    @staticmethod
    def _surface_forms(record: ReferenceRecord) -> list[str]:
        """Every string a claim might plausibly use to refer to this record.

        Offers deliberately expose only their own aliases (code, "offer CODE",
        full offer name). Their inherited product name belongs to the product.
        """
        if record.entity_type is EntityType.OFFER:
            return [alias for alias in record.aliases if alias]

        forms: list[str] = []
        for candidate in (record.product_name, record.plan_name):
            if candidate:
                forms.append(candidate)
        if record.brand and record.product_name:
            forms.append(f"{record.brand} {record.product_name}")
        if record.brand and record.plan_name:
            forms.append(f"{record.brand} {record.plan_name}")
        forms.extend(record.aliases)
        if record.sku:
            forms.append(record.sku)
        return [form for form in forms if form]

    # ------------------------------------------------------------------ #
    # ReferenceRepository
    # ------------------------------------------------------------------ #
    async def get_by_id(self, record_id: str) -> ReferenceRecord | None:
        self._assert_healthy()
        record = self._records.get(record_id)
        if record is not None:
            return record
        # Tolerate case/punctuation drift in caller-supplied identifiers.
        resolved = self._by_product_id.get(textutil.squash(record_id))
        return self._records.get(resolved) if resolved else None

    async def get_by_sku(self, sku: str) -> ReferenceRecord | None:
        self._assert_healthy()
        resolved = self._by_sku.get(textutil.squash(sku))
        return self._records.get(resolved) if resolved else None

    async def get_by_name(self, name: str) -> ReferenceRecord | None:
        """Exact (folded) name or alias lookup — the cheap path before search."""
        self._assert_healthy()
        resolved = self._by_name.get(textutil.fold(name))
        return self._records.get(resolved) if resolved else None

    async def search(self, query: str, *, limit: int = 5) -> list[tuple[ReferenceRecord, float]]:
        """Rank records by IDF-weighted token overlap with ``query``.

        Scoring is intentionally simple and explainable rather than embedding
        based. Entity resolution here is short-string identifier matching, where
        rare-token overlap is both stronger and auditable: a wrong match is
        traceable to a token, and there is no model to warm up or version.
        """
        self._assert_healthy()
        if limit <= 0:
            return []

        query_tokens = textutil.tokenize(query, drop_stopwords=True)
        if not query_tokens:
            return []

        total_docs = len(self._records)
        weights = {token: self._idf(token, total_docs) for token in set(query_tokens)}
        query_mass = sum(weights.values())
        if query_mass <= 0.0:
            return []

        candidates: set[str] = set()
        for token, weight in weights.items():
            postings = self._postings.get(token)
            if not postings or weight <= 0.0:
                continue
            if len(postings) / total_docs > _MAX_TOKEN_DOC_RATIO:
                continue
            candidates |= postings

        if not candidates:
            return []

        scored: list[tuple[ReferenceRecord, float]] = []
        for record_id in candidates:
            doc_tokens = self._doc_tokens[record_id]
            matched = sum(weight for token, weight in weights.items() if token in doc_tokens)
            coverage = matched / query_mass
            # Penalise records whose own tokens are largely unmatched, so a
            # long product name does not win on one rare shared token.
            precision = matched / max(
                sum(self._idf(token, total_docs) for token in doc_tokens), 1e-9
            )
            score = 0.75 * coverage + 0.25 * min(precision, 1.0)
            scored.append((self._records[record_id], min(score, 1.0)))

        scored.sort(key=lambda item: (-item[1], item[0].record_id))
        return scored[:limit]

    async def count(self) -> int:
        return len(self._records)

    def all_records(self) -> tuple[ReferenceRecord, ...]:
        """Every record, for startup-time derivation of vocabularies.

        Synchronous and eager because the only caller is composition-root
        setup (building the feature vocabulary), not request handling.
        """
        return tuple(self._records.values())

    async def health_check(self) -> bool:
        return self._healthy and bool(self._records)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _idf(self, token: str, total_docs: int) -> float:
        document_frequency = len(self._postings.get(token, ()))
        if document_frequency == 0:
            return 0.0
        return math.log((total_docs + 1) / (document_frequency + 0.5))

    def _assert_healthy(self) -> None:
        if not self._healthy:
            raise RepositoryError("reference repository is not initialized")

    def stats(self) -> dict[str, Any]:
        """Index statistics for the health and debug endpoints."""
        entity_counts: dict[str, int] = defaultdict(int)
        for record in self._records.values():
            entity_counts[record.entity_type.value] += 1
        return {
            "records": len(self._records),
            "distinct_tokens": len(self._postings),
            "indexed_skus": len(self._by_sku),
            "indexed_names": len(self._by_name),
            "by_entity_type": dict(sorted(entity_counts.items())),
        }
