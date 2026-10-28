# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""One ``CAPTURED`` Source per physical PDF page, plus page-level retrieval."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from pydantic import BaseModel, field_validator, model_validator
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation

SOURCE_TYPE = "pdf-page"
DEFINITION_VERSION = "1"
_WORD = re.compile(r"[a-z]+|\d+")
_STOPWORDS = frozenset([
    "a",
    "an",
    "the",
    "of",
    "and",
    "or",
    "in",
    "on",
    "for",
    "to",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "what",
    "which",
    "who",
    "whom",
    "how",
    "why",
    "when",
    "where",
    "does",
    "do",
    "did",
    "has",
    "have",
    "had",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "as",
    "at",
    "by",
    "from",
    "with",
    "about",
    "into",
    "than",
    "then",
    "so",
    "if",
    "not",
    "no",
    "we",
    "our",
    "you",
    "your",
    "they",
    "their",
    "there",
    "here",
    "can",
    "could",
    "should",
    "would",
    "may",
    "might",
    "must",
    "will",
    "shall",
    "per",
    "each",
    "also",
    "any",
    "all",
    "such",
    "other",
    "more",
    "most",
    "less",
    "least",
])
_BM25_K1 = 1.2
_BM25_B = 0.75
_STATEMENT = re.compile(
    r"(?i)(consolidated\s+(statements?|balance\s+sheets?)|statements?\s+of\s+"
    r"(income|operations|cash\s+flows|financial\s+position)|summary\s+of\s+"
    r"(financial\s+performance|business\s+segment)|table\s+\d+\s*:)"
)
_HEADING_LINE = re.compile(
    r"(?i)^(\d+\.\s+|item\s+\d|note\s+\d|part\s+[ivx]+|table\s+\d|consolidated|summary|business environment)"
)
_META_INSTRUCTION = re.compile(
    r"(?i)(answer in|respond to the question|give a response|assume the perspective|"
    r"if .*? then (?:please )?state)"
)
_METRIC_TERMS = frozenset([
    "margin",
    "revenue",
    "revenues",
    "sales",
    "income",
    "tax",
    "taxes",
    "liability",
    "liabilities",
    "asset",
    "assets",
    "debt",
    "cash",
    "flow",
    "flows",
    "operating",
    "profit",
    "ebitda",
    "capex",
    "expense",
    "expenses",
    "earnings",
    "eps",
    "balance",
    "sheet",
    "statement",
])
# Finance questions name metrics the statements themselves spell out differently.
_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "margin": (
        "gross margin",
        "operating margin",
        "income statement",
        "consolidated statements of income",
        "consolidated statements of operations",
        "operating profit",
    ),
    "tax": ("effective tax rate", "income taxes", "provision for income taxes", "income tax expense"),
    "liabilit": ("total liabilities", "balance sheet", "consolidated balance sheets"),
    "asset": ("total assets", "balance sheet", "consolidated balance sheets"),
    "revenue": (
        "net revenue",
        "net sales",
        "revenues",
        "consolidated statements of income",
        "statements of operations",
    ),
    "sales": ("net sales", "revenue", "consolidated statements of income", "statements of operations"),
    "income": ("operating income", "net income", "consolidated statements of income", "statements of operations"),
    "cash": ("cash flows", "consolidated statements of cash flows", "operating activities"),
    "segment": ("segment", "business segment data", "segment information", "revenues"),
    "legal": ("legal proceedings", "litigation", "claims"),
    "litigation": ("legal proceedings", "litigation"),
    "geograph": ("geographic", "north america", "latin america", "europe", "asia pacific"),
    "customer": ("customers", "customer", "business"),
    "cyclical": ("cyclical", "cyclicality"),
    "production": ("production rate", "production rates"),
    "liquid": ("liquidity", "quick ratio", "current assets", "current liabilities", "balance sheet"),
    "quick": ("quick ratio", "liquidity", "current assets", "current liabilities"),
    "product": ("products", "services", "business", "segment"),
    "service": ("services", "products", "business"),
    "retain": ("retain", "retention", "card member", "cardmembers", "customers"),
    "card": ("card member", "cardmembers", "customers", "retention"),
    "restructur": ("restructuring", "severance", "income statement", "consolidated statements of income"),
    "ebitda": ("operating income", "depreciation and amortization", "statement of cash flows", "income statement"),
    "capex": ("capital expenditures", "capital spending"),
}


class PDFDocumentInput(BaseModel):
    """Raw bytes of one complete PDF document."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        _validate_document_id(value)
        return value


class PDFPageInput(BaseModel):
    """One physical page of a PDF document, in reading order."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """A captured page whose identity binds the document revision and page number."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @model_validator(mode="after")
    def validate_identity(self) -> PDFPageSource:
        if self.name != self.source_id:
            raise ValueError("PDF page Source name must equal its source_id")  # noqa: TRY003
        return self


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Derive the stable identity of one page of one document revision."""

    _validate_document_id(document_id)
    if not isinstance(document_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", document_sha256) is None:
        raise ValueError("document_sha256 must be a lowercase 64-character hex digest")  # noqa: TRY003
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    identity = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(f"{document_id}\x00{document_sha256}\x00{page_number}".encode()).hexdigest()
        identity = f"pdf:sha256:{digest}"
    return identity


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Extract every physical page before returning any of them."""

    raw = value.content
    if not raw:
        raise ValueError("PDF document must not be empty")  # noqa: TRY003
    document_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a user password")  # noqa: TRY003, TRY301
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=document_sha256,
                page_number=number,
                text=(page.extract_text() or "").strip(),
            )
            for number, page in enumerate(reader.pages, start=1)
        )
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"PDF document could not be parsed: {error}") from error  # noqa: TRY003
    if not pages:
        raise ValueError("PDF document contains no pages")  # noqa: TRY003
    return pages


class _PDFPageAdapter:
    """Resolve parsed pages into Sources and read them back without loss."""

    name = SOURCE_TYPE
    input_class = PDFPageInput
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            definition_version=DEFINITION_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class _PDFPageTextEvidenceProjection:
    """Standard ``powercontext.text-evidence`` view of one page."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=SOURCE_TYPE,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: AdapterSourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = (
    AdapterSourceDefinition(
        adapter=_PDFPageAdapter(),
        version=DEFINITION_VERSION,
        projections=(_PDFPageTextEvidenceProjection(),),
    )
)


async def search_pdf_pages(
    memory: Any,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return exact citations for at most ``limit`` distinct evidence pages."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime.models import SearchMemoryRequest

    if limit <= 0 or not isinstance(question, str) or not question.strip():
        return ()

    records = await _scoped_entries(memory)
    if records:
        ranked = _rank_pages([record.entry.text for record in records], question)
        return tuple(records[index].citation for index in ranked[:limit])

    return await _lexical_fallback(memory, question, limit, SearchMemoryRequest, MemoryCitation)


async def _scoped_entries(memory: Any) -> list[Any]:
    """Read the scoped Memory head so every page can be scored locally."""

    try:
        page = await memory.list()
    except Exception:
        return []
    entries = getattr(page, "entries", ()) or ()
    return list(entries)


def _rank_pages(texts: list[str], question: str) -> list[int]:
    """Keep a named document's pages together when a scope holds several documents."""

    if not texts:
        return []
    core_question = _clean_question(question)
    question_tokens = set(_terms(core_question))
    question_compact = re.sub(r"[^a-z0-9]+", "", core_question.lower())
    document_ids = [text.split("\n", 1)[0].strip() for text in texts]
    matched = {
        document_id
        for document_id in set(document_ids)
        if document_id and _document_matches(document_id, question_tokens, question_compact)
    }
    if not matched:
        matched = _infer_document(document_ids, texts, core_question)
    if matched and len(matched) < len(set(document_ids)):
        active = [index for index, document_id in enumerate(document_ids) if document_id in matched]
        local = _rank_pages_unfiltered([texts[index] for index in active], question)
        return [active[index] for index in local]
    return _rank_pages_unfiltered(texts, question)


def _infer_document(  # noqa: C901
    document_ids: list[str], texts: list[str], question: str
) -> set[str]:
    """Use a rare query term to pick the document when the name is abbreviated."""

    unique = {document_id for document_id in document_ids if document_id}
    if len(unique) <= 1:
        return set()
    query_terms = set(_terms(question))
    if not query_terms:
        return set()
    counts = {document_id: Counter() for document_id in unique}
    lengths = dict.fromkeys(unique, 0)
    for document_id, text in zip(document_ids, texts, strict=True):
        if document_id not in counts:
            continue
        counter = Counter(_terms(text))
        counts[document_id].update(counter)
        lengths[document_id] += sum(counter.values())
    total_length = sum(lengths.values()) or 1.0
    average = total_length / len(unique)
    document_tokens = {document_id: set(counter) for document_id, counter in counts.items()}
    unique_candidates = {
        document_id
        for term in query_terms
        if len(term) >= 3
        for document_id in unique
        if term in document_tokens[document_id] and sum(1 for tokens in document_tokens.values() if term in tokens) == 1
    }
    if len(unique_candidates) == 1:
        return unique_candidates
    document_frequency = {term: sum(1 for counter in counts.values() if counter.get(term)) for term in query_terms}
    scores: dict[str, float] = dict.fromkeys(unique, 0.0)
    for term, frequency in document_frequency.items():
        if not frequency:
            continue
        weight = math.log((len(unique) - frequency + 0.5) / (frequency + 0.5) + 1.0)
        for document_id, counter in counts.items():
            term_frequency = counter.get(term)
            if not term_frequency:
                continue
            normalizer = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * lengths[document_id] / average)
            scores[document_id] += weight * (term_frequency * (_BM25_K1 + 1.0)) / (term_frequency + normalizer)
    ranked = sorted(scores, key=lambda document_id: (scores[document_id], document_id), reverse=True)
    if not ranked or scores[ranked[0]] <= 0.0:
        return set()
    second = scores[ranked[1]] if len(ranked) > 1 else 0.0
    if second <= 0.0 or scores[ranked[0]] >= 1.35 * second:
        return {ranked[0]}
    return set()


def _document_matches(document_id: str, question_tokens: set[str], question_compact: str) -> bool:
    prefix = re.split(r"[_\-\s.]+", document_id, maxsplit=1)[0].lower()
    compact = re.sub(r"[^a-z0-9]+", "", prefix)
    return bool(compact) and (
        compact in question_tokens
        or (len(compact) >= 5 and compact in question_compact)
        or (any(character.isdigit() for character in compact) and compact in question_compact)
    )


def _rank_pages_unfiltered(texts: list[str], question: str) -> list[int]:  # noqa: C901
    """Rank pages by query terms, finance synonyms, statements and sections."""

    n = len(texts)
    if not n:
        return []
    core_question = _clean_question(question)
    base_terms = _terms(core_question)
    query_terms = set(base_terms)
    for term in base_terms:
        for key, phrases in _QUERY_EXPANSIONS.items():
            if term.startswith(key):
                for phrase in phrases:
                    query_terms.update(_terms(phrase))

    counts = [_token_counts(text) for text in texts]
    lengths = [sum(counter.values()) for counter in counts]
    average = sum(lengths) / n or 1.0
    document_frequency: dict[str, int] = {}
    for counter in counts:
        for term in counter:
            document_frequency[term] = document_frequency.get(term, 0) + 1
    inverse_frequency = {
        term: math.log((n - frequency + 0.5) / (frequency + 0.5) + 1.0)
        for term, frequency in document_frequency.items()
    }

    scores = [0.0] * n
    for term in query_terms:
        weight = inverse_frequency.get(term)
        if weight is None:
            continue
        for index, counter in enumerate(counts):
            frequency = counter.get(term)
            if not frequency:
                continue
            normalizer = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * lengths[index] / average)
            scores[index] += weight * (frequency * (_BM25_K1 + 1.0)) / (frequency + normalizer)

    headings = [_heading(text) for text in texts]
    heading_counts = [_token_counts(heading) for heading in headings]
    for term in query_terms:
        weight = inverse_frequency.get(term)
        if weight is None:
            continue
        for index, counter in enumerate(heading_counts):
            if counter.get(term):
                scores[index] += 4.0 * weight

    metric_question = any(term in _METRIC_TERMS for term in base_terms)
    statement_question = any(
        term.startswith(("capital", "inventor", "liquid", "quick")) or term == "turnover" for term in base_terms
    )
    for index, heading in enumerate(headings):
        if _STATEMENT.search(heading):
            scores[index] += 3.0 * (1.5 if metric_question else 0.7)
            if statement_question:
                scores[index] += 20.0

    flattened = [" ".join(_WORD.findall(text.lower())) for text in texts]
    heading_flattened = [" ".join(_WORD.findall(heading.lower())) for heading in headings]
    expansion_phrases: set[str] = set()
    for term in base_terms:
        for key, phrases in _QUERY_EXPANSIONS.items():
            if term.startswith(key):
                expansion_phrases.update(phrases)
    for phrase in expansion_phrases:
        normalized = " ".join(_WORD.findall(phrase.lower()))
        if not normalized:
            continue
        for index in range(n):
            if f" {normalized} " in f" {flattened[index]} ":
                scores[index] += 1.5
            if f" {normalized} " in f" {heading_flattened[index]} ":
                for neighbor in range(max(0, index - 2), min(n, index + 3)):
                    scores[neighbor] += 2.0

    question_tokens = set(base_terms)
    question_compact = re.sub(r"[^a-z0-9]+", "", core_question.lower())
    for index, text in enumerate(texts):
        document_id = text.split("\n", 1)[0].strip()
        if not document_id:
            continue
        prefix = re.split(r"[_\-\s.]+", document_id, maxsplit=1)[0].lower()
        compact = re.sub(r"[^a-z0-9]+", "", prefix)
        if compact and (compact in question_tokens or (len(compact) >= 5 and compact in question_compact)):
            scores[index] += 1000.0

    return sorted(range(n), key=lambda index: (scores[index], -index), reverse=True)


def _clean_question(question: str) -> str:
    kept = [sentence for sentence in re.split(r"(?<=[.!?])\s+", question) if not _META_INSTRUCTION.search(sentence)]
    return " ".join(kept) or question


def _terms(text: str) -> list[str]:
    return [term for term in _WORD.findall(text.lower()) if term not in _STOPWORDS and len(term) > 1]


def _heading(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip() and line.strip() != "Table of Contents"]
    selected = lines[:8]
    for line in lines:
        if _HEADING_LINE.match(line):
            selected.append(line)
    return "\n".join(selected)


def _token_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for term in _terms(text):
        counts[term] = counts.get(term, 0) + 1
    return counts


async def _lexical_fallback(
    memory: Any,
    question: str,
    limit: int,
    request_class: Any,
    citation_class: Any,
) -> tuple[Any, ...]:
    """Use the engine's lexical channel when the scope cannot be enumerated."""

    page = await memory.search(request_class(query=question, mode="fts", limit=max(limit * 20, 120)))
    citations: list[Any] = []
    seen: set[str] = set()
    for hit in page.hits:
        if hit.entry_id in seen:
            continue
        seen.add(hit.entry_id)
        citations.append(
            citation_class(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
        )
        if len(citations) >= limit:
            break
    return tuple(citations)


def _validate_document_id(value: str) -> None:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
