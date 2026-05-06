"""AI analysis pipeline for Cross-check: chunking, topic modelling, and inconsistency checking."""

import logging
import os
import re
from pathlib import Path

from typing import Any, cast

import numpy as np
from bertopic import BERTopic
from pydantic import BaseModel
from pydantic_ai import Agent
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import CountVectorizer

logger = logging.getLogger(__name__)

ANALYSIS_MODEL = os.getenv("ANALYSIS_MODEL", "openai:gpt-4.1-mini")
MIN_CHUNKS_FOR_TOPIC_MODEL = 20
MIN_CHUNK_CHARS = 50
TARGET_CHUNK_WORDS = 300
MAX_PASSAGES_PER_TOPIC = 10
MAX_SPLIT_DEPTH = 3

_embedding_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info("Loading sentence-transformers embedding model...")
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Embedding model loaded.")
    return _embedding_model


# ---------------------------------------------------------------------------
# Pydantic output models
# ---------------------------------------------------------------------------


class RelevantPassage(BaseModel):
    document: str
    passage: str


class Inconsistency(BaseModel):
    type: str  # "contradiction" or "uneven_coverage"
    description: str
    documents_involved: list[str]
    relevant_passages: list[RelevantPassage]


class InconsistencyResult(BaseModel):
    has_inconsistencies: bool
    inconsistencies: list[Inconsistency]


# ---------------------------------------------------------------------------
# Chunk data structure
# ---------------------------------------------------------------------------


class Chunk(BaseModel):
    text: str
    source_file: str
    chunk_idx: int


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_documents(session_dir: Path) -> list[Chunk]:
    """Load all markdown files from a session directory and split into chunks."""
    chunks: list[Chunk] = []
    for md_file in sorted(session_dir.glob("*.md")):
        if md_file.name == "session.json":
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            logger.warning(f"Could not read {md_file.name}, skipping")
            continue

        file_chunks = _chunk_text(text, md_file.name)
        chunks.extend(file_chunks)

    logger.info(f"Chunked {len(chunks)} passages from {session_dir}")
    return chunks


def _chunk_text(text: str, source_file: str) -> list[Chunk]:
    """Split a markdown document into chunks of roughly TARGET_CHUNK_WORDS words."""
    paragraphs = re.split(r"\n{2,}", text.strip())
    paragraphs = [p.strip() for p in paragraphs if len(p.strip()) >= MIN_CHUNK_CHARS]

    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_words = 0
    chunk_idx = 0

    for para in paragraphs:
        word_count = len(para.split())
        if current_words + word_count > TARGET_CHUNK_WORDS and current_parts:
            chunks.append(
                Chunk(
                    text="\n\n".join(current_parts),
                    source_file=source_file,
                    chunk_idx=chunk_idx,
                )
            )
            chunk_idx += 1
            current_parts = []
            current_words = 0
        current_parts.append(para)
        current_words += word_count

    if current_parts:
        chunks.append(
            Chunk(
                text="\n\n".join(current_parts),
                source_file=source_file,
                chunk_idx=chunk_idx,
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Text cleaning (for embedding and topic modelling only — not for LLM prompts)
# ---------------------------------------------------------------------------

# Matches markdown links [text](url) — keep the link text, drop the URL
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Matches bare http(s) URLs
_URL_RE = re.compile(r"https?://\S+")


def _clean_for_modelling(text: str) -> str:
    """Strip URLs and markdown link syntax for cleaner topic labels and embeddings.

    Full URLs are preserved in the original chunk text used by the LLM.
    """
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def embed_chunks(chunks: list[Chunk]) -> np.ndarray:
    """Embed a list of chunks using the local sentence-transformers model."""
    model = get_embedding_model()
    texts = [_clean_for_modelling(c.text) for c in chunks]
    embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    return embeddings  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Topic modelling
# ---------------------------------------------------------------------------


class TopicChunk(BaseModel):
    text: str
    source_file: str


class TopicInfo(BaseModel):
    id: int
    label: str
    chunk_count: int
    doc_count: int
    docs: list[str]
    chunk_indices: list[int]
    topic_chunks: list[TopicChunk] = []


def _topic_cohesion(embeddings: np.ndarray, indices: list[int]) -> float:
    """Mean cosine similarity of each chunk embedding to the topic centroid.

    Higher = tighter cluster = passages more genuinely about the same thing.
    """
    vecs = embeddings[indices]
    centroid = vecs.mean(axis=0)
    centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-8)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8
    sims = (vecs / norms) @ centroid_norm
    return float(sims.mean())


def _label_from_indices(indices: list[int], chunks: list[Chunk]) -> str:
    """Derive a keyword label from a subset of chunks via term frequency."""
    texts = [_clean_for_modelling(chunks[i].text) for i in indices]
    try:
        vec = CountVectorizer(stop_words="english", ngram_range=(1, 2), max_features=20)
        X = vec.fit_transform(texts)
        scores = np.asarray(X.sum(axis=0)).flatten()
        terms = vec.get_feature_names_out()
        top = sorted(zip(scores, terms), reverse=True)[:3]
        return " · ".join(t for _, t in top)
    except Exception:
        return ""


def _bertopic_split(
    global_indices: list[int],
    chunks: list[Chunk],
    embeddings: np.ndarray,
) -> dict[int, list[int]]:
    """Run a single BERTopic pass on a subset; returns {topic_id: [global_indices]}."""
    sub_embeddings = embeddings[global_indices]
    texts = [_clean_for_modelling(chunks[i].text) for i in global_indices]
    model = BERTopic(
        vectorizer_model=CountVectorizer(stop_words="english", ngram_range=(1, 2)),
        min_topic_size=2,
        calculate_probabilities=False,
        verbose=False,
    )
    sub_topics, _ = model.fit_transform(texts, sub_embeddings)
    groups: dict[int, list[int]] = {}
    for local_idx, topic_id in enumerate(sub_topics):
        groups.setdefault(topic_id, []).append(global_indices[local_idx])
    return groups


def _recursive_split(
    global_indices: list[int],
    chunks: list[Chunk],
    embeddings: np.ndarray,
    depth: int = 0,
) -> list[list[int]]:
    """Recursively split a cluster until every group has ≤ MAX_PASSAGES_PER_TOPIC chunks."""
    if len(global_indices) <= MAX_PASSAGES_PER_TOPIC or depth >= MAX_SPLIT_DEPTH:
        return [global_indices]

    try:
        groups = _bertopic_split(global_indices, chunks, embeddings)
    except Exception:
        return [global_indices]

    real = {k: v for k, v in groups.items() if k != -1}
    if len(real) <= 1:
        # BERTopic couldn't split further; keep as-is
        return [global_indices]

    result: list[list[int]] = []
    for sub_indices in real.values():
        result.extend(_recursive_split(sub_indices, chunks, embeddings, depth + 1))

    # Outliers: attempt to split if large, otherwise keep
    if -1 in groups and groups[-1]:
        outliers = groups[-1]
        result.extend(_recursive_split(outliers, chunks, embeddings, depth + 1))

    return result


def run_topic_model(chunks: list[Chunk], embeddings: np.ndarray) -> list[TopicInfo]:
    """
    Cluster chunks into topics using BERTopic, then recursively split any topic
    with more than MAX_PASSAGES_PER_TOPIC chunks.
    Falls back to a single synthetic topic for small collections.
    Topics are sorted by internal cohesion descending (tightest clusters first).
    """
    if len(chunks) < MIN_CHUNKS_FOR_TOPIC_MODEL:
        logger.info(
            f"Only {len(chunks)} chunks — skipping BERTopic, using single topic fallback"
        )
        return _single_topic_fallback(chunks)

    min_topic_size = max(2, min(5, int(len(chunks) ** 0.5) // 3))
    topic_model = BERTopic(
        vectorizer_model=CountVectorizer(stop_words="english", ngram_range=(1, 2)),
        min_topic_size=min_topic_size,
        calculate_probabilities=False,
        verbose=False,
    )

    texts = [_clean_for_modelling(c.text) for c in chunks]
    topics, _ = topic_model.fit_transform(texts, embeddings)

    # Group initial assignments (drop outliers at top level)
    initial_groups: dict[int, list[int]] = {}
    for i, t in enumerate(topics):
        if t != -1:
            initial_groups.setdefault(t, []).append(i)

    results: list[TopicInfo] = []
    topic_counter = 0

    for topic_id, group_indices in initial_groups.items():
        # Label from BERTopic's own keyword extraction
        topic_words = cast(list[tuple[str, float]], topic_model.get_topic(topic_id))
        base_label = " · ".join(w for w, _ in topic_words[:3]) if topic_words else ""

        # Recursively split if oversized
        if len(group_indices) > MAX_PASSAGES_PER_TOPIC:
            final_groups = _recursive_split(group_indices, chunks, embeddings)
        else:
            final_groups = [group_indices]

        for idx_group in final_groups:
            if len(idx_group) < 2:
                continue
            matched_chunks = [chunks[i] for i in idx_group]
            docs = sorted({c.source_file for c in matched_chunks})
            if len(docs) < 2:
                continue

            # Re-derive label for sub-groups; fall back to parent label
            if len(final_groups) > 1:
                label = _label_from_indices(idx_group, chunks) or base_label
            else:
                label = base_label or _label_from_indices(idx_group, chunks)

            results.append(
                TopicInfo(
                    id=topic_counter,
                    label=label or f"Topic {topic_counter}",
                    chunk_count=len(idx_group),
                    doc_count=len(docs),
                    docs=docs,
                    chunk_indices=idx_group,
                    topic_chunks=[
                        TopicChunk(text=c.text, source_file=c.source_file)
                        for c in matched_chunks
                    ],
                )
            )
            topic_counter += 1

    results.sort(
        key=lambda t: _topic_cohesion(embeddings, t.chunk_indices), reverse=True
    )
    logger.info(f"BERTopic found {len(results)} topics after recursive splitting")
    return results


def _single_topic_fallback(chunks: list[Chunk]) -> list[TopicInfo]:
    docs = sorted({c.source_file for c in chunks})
    if len(docs) < 2:
        return []
    return [
        TopicInfo(
            id=0,
            label="All documents",
            chunk_count=len(chunks),
            doc_count=len(docs),
            docs=docs,
            chunk_indices=list(range(len(chunks))),
            topic_chunks=[
                TopicChunk(text=c.text, source_file=c.source_file) for c in chunks
            ],
        )
    ]


# ---------------------------------------------------------------------------
# Inconsistency check (LLM)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert content analyst reviewing a collection of web content or \
documents for inconsistencies. Your bar for reporting should be high — only \
flag things that would genuinely mislead or confuse a reader.

Definitions:
- Contradiction: Document A explicitly states X, Document B explicitly states \
  Y, and X and Y conflict on the same factual claim.
- Uneven coverage: Document A contains substantive information on a point that \
  Document B omits AND does not link to — leaving a reader of Document B \
  without something they genuinely need.

Do NOT flag as uneven coverage:
- A document that is intentionally high-level, introductory, or a summary — \
  it is expected to omit detail.
- A document that links to further information on the point, even if it does \
  not include the detail inline. A link is adequate coverage.
- A document that covers a topic briefly when its purpose clearly does not \
  require depth (e.g. a form, a byelaw, a vehicle checklist).
- Uniform omissions where all documents treat something the same way.
- Navigational elements, footers, cookie notices, or boilerplate.
- Information whose absence would not mislead a reader who follows the \
  document's natural links.

Only flag uneven coverage when one document contains detail that another \
actively needs but neither includes nor links to, and a reader would be \
materially worse off as a result.

Return an empty inconsistencies array if nothing clearly meets this bar. \
Respond only with valid JSON matching the requested structure.\
"""

_inconsistency_agent: Any = None


def _get_agent() -> Any:
    global _inconsistency_agent
    if _inconsistency_agent is None:
        _inconsistency_agent = Agent(
            ANALYSIS_MODEL,
            output_type=InconsistencyResult,
            system_prompt=_SYSTEM_PROMPT,
        )
    return _inconsistency_agent


async def check_topic_inconsistencies(
    topic: TopicInfo, all_chunks: list[Chunk]
) -> InconsistencyResult:
    """Run the LLM inconsistency check for a single topic cluster."""
    topic_chunks = [all_chunks[i] for i in topic.chunk_indices]

    # Group chunks by source document
    by_doc: dict[str, list[str]] = {}
    for chunk in topic_chunks:
        by_doc.setdefault(chunk.source_file, []).append(chunk.text)

    if len(by_doc) < 2:
        # Can't have inconsistencies across documents if only one document covers the topic
        return InconsistencyResult(has_inconsistencies=False, inconsistencies=[])

    # Build prompt
    passages_text = ""
    for doc_name, doc_chunks in by_doc.items():
        passages_text += f"\n=== {doc_name} ===\n"
        passages_text += "\n\n".join(doc_chunks)
        passages_text += "\n"

    prompt = f"""\
These passages all relate to the topic: "{topic.label}"
They come from {len(by_doc)} different documents in the same content collection.

{passages_text}
===

Look for genuine inconsistencies between these documents. Before reporting \
anything, ask yourself:
- For contradictions: do both documents make an explicit, conflicting claim \
  about the same fact?
- For uneven coverage: does the document that omits the information also fail \
  to link to it? Is the omitting document the kind of page where a reader \
  would reasonably expect to find this detail (not a summary, form, or \
  high-level overview)? Would a reader be materially misled without it?

If the answer to any of these questions is no, do not flag it.

Return your findings as a JSON object with has_inconsistencies (bool) and \
inconsistencies (array). Each inconsistency should have: type \
("contradiction" or "uneven_coverage"), description (one clear sentence \
naming which documents differ and how), documents_involved (list of \
filenames), and relevant_passages (list of objects with document and passage \
fields quoting the relevant text). Return an empty array if nothing clearly \
meets the bar.\
"""

    agent = _get_agent()
    result = await agent.run(prompt)
    return result.output
