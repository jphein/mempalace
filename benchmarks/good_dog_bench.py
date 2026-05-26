#!/usr/bin/env python3
"""
MemPal × good-dog-corpus Benchmark
====================================

Evaluates MemPal's retrieval against the multipass-structural-memory-eval
good-dog-corpus (24 questions, 23 vault notes, 6 SME categories).

Unlike LongMemEval (one throwaway palace per question), this corpus is small
and shared across all questions — we build one palace per mode, then query it
24 times. Each vault note becomes one document.

Scoring uses the SME convention: a question's `expected_sources` are
substrings that should appear in the concatenated context_string of the
top-K retrieved documents. Per-question recall = fraction of expected
substrings matched. Per-category recall = mean over questions in that
category.

Modes:
    raw     — baseline: raw vault text into ChromaDB (default)
    aaak    — AAAK dialect compression before ingestion
    rooms   — use the note's vault subdirectory as its room; soft-boost
              matching-room results

Usage:
    python benchmarks/good_dog_bench.py CORPUS_DIR
    python benchmarks/good_dog_bench.py CORPUS_DIR --mode aaak
    python benchmarks/good_dog_bench.py CORPUS_DIR --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import chromadb
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# Corpus loading
# =============================================================================


def load_vault(corpus_dir: Path) -> list[dict]:
    """
    Walk corpus_dir/vault/**/*.md, return a list of documents.

    Each document is:
        {
            "id":     "<domain>/<filename-without-extension>",
            "path":   absolute Path,
            "domain": top-level subdir under vault/ (e.g. "nutrition_safety"),
            "text":   full file content (frontmatter + body),
        }
    """
    vault_dir = corpus_dir / "vault"
    if not vault_dir.is_dir():
        raise FileNotFoundError(f"vault directory not found at {vault_dir}")

    docs = []
    for md in sorted(vault_dir.rglob("*.md")):
        rel = md.relative_to(vault_dir)
        domain = rel.parts[0] if len(rel.parts) > 1 else "_root"
        doc_id = f"{domain}/{md.stem}"
        text = md.read_text(encoding="utf-8")
        docs.append(
            {
                "id": doc_id,
                "path": md,
                "domain": domain,
                "text": text,
            }
        )
    return docs


def load_questions(corpus_dir: Path) -> list[dict]:
    qs_path = corpus_dir / "questions.yaml"
    with qs_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["questions"]


# =============================================================================
# Embedding + collection (mirrors longmemeval_bench.py pattern)
# =============================================================================


_bench_client = chromadb.EphemeralClient()
_bench_embed_fn = None


def _make_embed_fn(model_name: str):
    if model_name == "default" or not model_name:
        return None

    MODEL_MAP = {
        "bge-base": "BAAI/bge-base-en-v1.5",
        "bge-large": "BAAI/bge-large-en-v1.5",
        "nomic": "nomic-ai/nomic-embed-text-v1.5",
        "mxbai": "mixedbread-ai/mxbai-embed-large-v1",
    }
    hf_name = MODEL_MAP.get(model_name, model_name)

    try:
        from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
        from fastembed import TextEmbedding

        class _FastEmbedFn(EmbeddingFunction):
            def __init__(self, name):
                print(f"  Loading embedding model: {name}...")
                self._model = TextEmbedding(name)
                print("  Model ready.")

            def __call__(self, input: Documents) -> Embeddings:
                return [list(vec) for vec in self._model.embed(input)]

        return _FastEmbedFn(hf_name)
    except ImportError:
        print("ERROR: fastembed not installed. Falling back to default model.")
        return None


def _fresh_collection(name: str = "good_dog_drawers"):
    try:
        _bench_client.delete_collection(name)
    except Exception:
        pass
    if _bench_embed_fn is not None:
        return _bench_client.create_collection(name, embedding_function=_bench_embed_fn)
    return _bench_client.create_collection(name)


# =============================================================================
# Ingestion + retrieval per mode
# =============================================================================

# Reuse the same topic-keyword scheme as longmemeval_bench so cross-mode
# behaviour stays comparable.  For good-dog-corpus the natural "room" is
# the vault subdirectory (domain) — we use both: domain-as-room AND a
# question-side topic detection on the question text.
TOPIC_KEYWORDS = {
    "veterinary_research": [
        "fda",
        "dcm",
        "cardiomyopathy",
        "diet",
        "tufts",
        "freeman",
        "petfoodology",
        "research",
        "investigation",
    ],
    "behavioral_research": [
        "alpha",
        "wolf",
        "dominance",
        "training",
        "schenkel",
        "mech",
        "avsab",
        "reinforcement",
        "behavior",
    ],
    "breed_standards": [
        "akc",
        "ukc",
        "breed",
        "standard",
        "pit bull",
        "shepherd",
        "alsatian",
        "kennel",
        "club",
    ],
    "municipal_policy": [
        "ontario",
        "montreal",
        "calgary",
        "aurora",
        "dola",
        "bsl",
        "bylaw",
        "repeal",
        "ban",
        "municipal",
        "city",
        "policy",
        "statute",
    ],
    "nutrition_safety": [
        "hills",
        "hill's",
        "vitamin d",
        "recall",
        "premix",
        "warning letter",
        "topeka",
        "nutrition",
        "toxicity",
    ],
    "community_journalism": [
        "npr",
        "nbc",
        "cpr",
        "shelter",
        "adoption",
        "coverage",
        "news",
        "reported",
    ],
}


def detect_room_for_text(text: str) -> str:
    text_lower = text[:3000].lower()
    scores = {}
    for room, keywords in TOPIC_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[room] = score
    if scores:
        return max(scores, key=scores.get)
    return "general"


def build_palace(docs: list[dict], mode: str):
    """
    Ingest the vault into a fresh collection according to mode.
    Returns (collection, ingest_docs) where ingest_docs is a list of dicts
    holding `id`, `domain`, `room`, and the ingested text (raw or AAAK).
    The original `docs[i]["text"]` is preserved for scoring against retrieved
    documents, since AAAK retrieval still scores on the raw vault content.
    """
    collection = _fresh_collection()

    ingest_texts = []
    ingest_ids = []
    ingest_metas = []

    if mode == "aaak":
        from mempalace.dialect import Dialect

        dialect = Dialect()

    for i, d in enumerate(docs):
        room = d["domain"] if mode == "rooms" else detect_room_for_text(d["text"])
        if mode == "aaak":
            ingest_text = dialect.compress(d["text"])
        else:
            ingest_text = d["text"]
        ingest_texts.append(ingest_text)
        ingest_ids.append(f"doc_{i}")
        ingest_metas.append(
            {
                "doc_id": d["id"],
                "domain": d["domain"],
                "room": room,
            }
        )

    collection.add(
        documents=ingest_texts,
        ids=ingest_ids,
        metadatas=ingest_metas,
    )

    return collection


def query_palace(
    collection,
    docs: list[dict],
    question_text: str,
    mode: str,
    n_results: int,
):
    """
    Query the palace and return ranked indices into `docs` (descending
    relevance), where indices correspond to the original vault doc order.

    For modes that score the retrieved text against `expected_sources`,
    callers should look up the *raw* doc text from docs[idx]["text"], not
    the ingested text (which may be AAAK-compressed).
    """
    if mode == "rooms":
        query_room = detect_room_for_text(question_text)
        results = collection.query(
            query_texts=[question_text],
            n_results=min(n_results, len(docs)),
            include=["distances", "metadatas"],
        )
        doc_id_to_idx = {f"doc_{i}": i for i in range(len(docs))}
        scored = []
        for rid, dist, meta in zip(
            results["ids"][0],
            results["distances"][0],
            results["metadatas"][0],
        ):
            idx = doc_id_to_idx[rid]
            boosted = dist * 0.8 if meta.get("room") == query_room else dist
            scored.append((idx, boosted))
        scored.sort(key=lambda x: x[1])
        ranked = [idx for idx, _ in scored]
    else:
        results = collection.query(
            query_texts=[question_text],
            n_results=min(n_results, len(docs)),
            include=["distances"],
        )
        doc_id_to_idx = {f"doc_{i}": i for i in range(len(docs))}
        ranked = [doc_id_to_idx[rid] for rid in results["ids"][0]]

    # Fill in any unranked indices (shouldn't happen at n_results>=len(docs)).
    seen = set(ranked)
    for i in range(len(docs)):
        if i not in seen:
            ranked.append(i)

    return ranked


# =============================================================================
# Scoring
# =============================================================================


def question_recall(
    docs: list[dict],
    ranked_indices: list[int],
    expected_sources: list[str],
    k: int,
) -> tuple[float, list[str]]:
    """
    Fraction of `expected_sources` substrings that appear in the
    concatenated raw text of the top-k retrieved docs (case-insensitive).
    Returns (recall, missing_substrings).
    """
    if not expected_sources:
        return 1.0, []

    context = "\n\n".join(docs[idx]["text"] for idx in ranked_indices[:k])
    context_lower = context.lower()
    hits = []
    misses = []
    for src in expected_sources:
        if src.lower() in context_lower:
            hits.append(src)
        else:
            misses.append(src)
    recall = len(hits) / len(expected_sources)
    return recall, misses


# =============================================================================
# Main
# =============================================================================


def run_mode(
    docs: list[dict],
    questions: list[dict],
    mode: str,
    ks: list[int],
    jsonl_out: Path | None = None,
):
    collection = build_palace(docs, mode)

    # Per-question, per-k recall
    per_question = []
    per_k_recalls = {k: [] for k in ks}
    per_category = defaultdict(lambda: defaultdict(list))  # cat -> k -> [recall]

    for q in questions:
        qid = q["id"]
        text = q["text"]
        expected = q.get("expected_sources", [])
        category = q.get("sme_category", "uncategorised")

        ranked = query_palace(collection, docs, text, mode, n_results=max(ks))

        per_k = {}
        for k in ks:
            recall, misses = question_recall(docs, ranked, expected, k)
            per_k_recalls[k].append(recall)
            per_category[category][k].append(recall)
            per_k[k] = {"recall": recall, "missing": misses}

        per_question.append(
            {
                "id": qid,
                "text": text,
                "category": category,
                "expected_sources": expected,
                "ranked_doc_ids": [docs[idx]["id"] for idx in ranked[: max(ks)]],
                "per_k": per_k,
            }
        )

    summary = {
        "mode": mode,
        "n_questions": len(questions),
        "n_docs": len(docs),
        "ks": ks,
        "mean_recall_per_k": {
            k: sum(per_k_recalls[k]) / len(per_k_recalls[k]) if per_k_recalls[k] else 0.0
            for k in ks
        },
        "per_category": {
            cat: {
                "n": len(per_category[cat][ks[0]]),
                "mean_recall_per_k": {
                    k: sum(per_category[cat][k]) / len(per_category[cat][k])
                    for k in ks
                },
            }
            for cat in sorted(per_category)
        },
    }

    if jsonl_out is not None:
        with jsonl_out.open("w", encoding="utf-8") as f:
            for entry in per_question:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return summary, per_question


def fmt_pct(x: float) -> str:
    return f"{x:.3f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "corpus_dir",
        type=Path,
        help="Path to good-dog-corpus directory (contains vault/ and questions.yaml)",
    )
    parser.add_argument(
        "--mode",
        choices=["raw", "aaak", "rooms"],
        default="raw",
        help="Retrieval mode (default: raw)",
    )
    parser.add_argument(
        "--embed-model",
        default="default",
        help="Embedding model (default: ChromaDB default = all-MiniLM-L6-v2)",
    )
    parser.add_argument(
        "--ks",
        default="1,3,5,10",
        help="Comma-separated list of K values to report (default: 1,3,5,10)",
    )
    parser.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Write per-question results to this JSONL path",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Write summary metrics to this JSON path",
    )
    args = parser.parse_args()

    global _bench_embed_fn
    _bench_embed_fn = _make_embed_fn(args.embed_model)

    ks = [int(x) for x in args.ks.split(",") if x.strip()]

    docs = load_vault(args.corpus_dir)
    questions = load_questions(args.corpus_dir)

    print(f"good-dog-corpus benchmark — mode={args.mode}")
    print(f"  Vault docs: {len(docs)}")
    print(f"  Questions:  {len(questions)}")
    print(f"  Ks:         {ks}")
    print()

    t0 = datetime.now()
    summary, per_question = run_mode(docs, questions, args.mode, ks, args.jsonl)
    elapsed = (datetime.now() - t0).total_seconds()

    summary["elapsed_seconds"] = elapsed
    summary["embed_model"] = args.embed_model

    print("=" * 60)
    print(f"Mode: {args.mode}   Elapsed: {elapsed:.1f}s")
    print("=" * 60)
    print()
    print("Overall recall (fraction of expected_sources matched):")
    for k in ks:
        print(f"  Recall@{k:<2} = {fmt_pct(summary['mean_recall_per_k'][k])}")
    print()
    print("Per-category recall:")
    cat_order = sorted(summary["per_category"])
    header = f"  {'category':<22} {'n':>3} " + " ".join(f"R@{k:<2}" for k in ks)
    print(header)
    print(f"  {'-' * (len(header) - 2)}")
    for cat in cat_order:
        row = summary["per_category"][cat]
        line = f"  {cat:<22} {row['n']:>3} " + " ".join(
            f"{row['mean_recall_per_k'][k]:.3f}" for k in ks
        )
        print(line)
    print()

    if args.json:
        args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(f"Wrote summary: {args.json}")
    if args.jsonl:
        print(f"Wrote per-question results: {args.jsonl}")


if __name__ == "__main__":
    main()
