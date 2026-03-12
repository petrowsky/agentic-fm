#!/usr/bin/env python3
"""Index Agentic FM documents into ChromaDB for RAG retrieval."""

import json
import os
import re
import sys
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/
CHROMA_DIR = Path(__file__).resolve().parent / "chroma_db"
COLLECTION_NAME = "agentic_fm"


def get_embedding_fn():
    return OllamaEmbeddingFunction(
        url="http://localhost:11434",
        model_name="nomic-embed-text",
    )


def chunk_markdown(filepath: Path) -> list[dict]:
    """Split a markdown file by ## headers into chunks."""
    text = filepath.read_text(encoding="utf-8")
    title = filepath.stem.replace("-", " ").title()

    # Extract H1 title if present
    h1_match = re.match(r"^#\s+(.+)", text)
    if h1_match:
        title = h1_match.group(1).strip()

    # Split on ## headers
    sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)
    chunks = []
    for section in sections:
        section = section.strip()
        if not section or len(section) < 20:
            continue

        # Extract section header if present
        header_match = re.match(r"^## (.+)", section)
        section_name = header_match.group(1).strip() if header_match else "Introduction"

        chunk_text = f"# {title}\n\n{section}"
        chunks.append({
            "text": chunk_text,
            "metadata": {
                "type": "docs",
                "source": filepath.name,
                "section": section_name,
            },
        })

    # If no ## headers found, treat the whole file as one chunk
    if not chunks and len(text.strip()) > 20:
        chunks.append({
            "text": text,
            "metadata": {
                "type": "docs",
                "source": filepath.name,
                "section": "Full document",
            },
        })

    return chunks


def chunk_step_catalog(filepath: Path) -> list[dict]:
    """Convert each step catalog entry into a text chunk."""
    data = json.loads(filepath.read_text(encoding="utf-8"))
    chunks = []

    for entry in data:
        name = entry.get("name", "Unknown")
        step_id = entry.get("id", "?")
        category = entry.get("category", "unknown")
        hr_sig = entry.get("hrSignature", "")
        status = entry.get("status", "")
        help_url = entry.get("helpUrl", "")
        snippet_file = entry.get("snippetFile", "")
        self_closing = entry.get("selfClosing", False)
        block_pair = entry.get("blockPair")

        # Build parameter descriptions
        param_lines = []
        for p in entry.get("params", []):
            req = "required" if p.get("required") else "optional"
            p_type = p.get("type", "unknown")
            label = p.get("hrLabel") or p.get("xmlElement", "")
            line = f"  - {label} ({p_type}, {req})"
            if p.get("enumValues"):
                vals = [str(v) if not isinstance(v, dict) else v.get("name", str(v)) for v in p["enumValues"]]
                line += f" values: {', '.join(vals)}"
            if p.get("defaultValue"):
                line += f" default: {p['defaultValue']}"
            param_lines.append(line)

        text_parts = [
            f"Step: {name} (id: {step_id}, category: {category})",
            f"HR Signature: {hr_sig}",
        ]
        if param_lines:
            text_parts.append("Parameters:\n" + "\n".join(param_lines))
        if self_closing:
            text_parts.append("Self-closing: yes (no child elements)")
        if block_pair:
            text_parts.append(f"Block pair: {block_pair}")
        if snippet_file:
            text_parts.append(f"Snippet file: {snippet_file}")
        if help_url:
            text_parts.append(f"Reference: {help_url}")

        chunks.append({
            "text": "\n".join(text_parts),
            "metadata": {
                "type": "catalog",
                "source": "step-catalog-en.json",
                "step_name": name,
                "category": category,
                "step_id": str(step_id),
            },
        })

    return chunks


def chunk_xml_snippets(snippets_dir: Path) -> list[dict]:
    """Each XML snippet file becomes one chunk."""
    chunks = []
    for xml_file in sorted(snippets_dir.rglob("*.xml")):
        text = xml_file.read_text(encoding="utf-8").strip()
        if not text:
            continue

        # Derive category and step name from path
        rel = xml_file.relative_to(snippets_dir)
        parts = rel.parts
        category = parts[0] if len(parts) > 1 else "uncategorized"
        step_name = xml_file.stem

        chunk_text = f"XML Snippet: {step_name} (category: {category})\n\n{text}"
        chunks.append({
            "text": chunk_text,
            "metadata": {
                "type": "snippet",
                "source": str(rel),
                "step_name": step_name,
                "category": category,
            },
        })

    return chunks


def build_index():
    """Build the full ChromaDB index from all document sources."""
    print("Building RAG index for Agentic FM...")

    # Collect all chunks
    all_chunks = []

    # 1. Knowledge base markdown docs
    knowledge_dir = AGENT_DIR / "docs" / "knowledge"
    if knowledge_dir.exists():
        for md_file in sorted(knowledge_dir.glob("*.md")):
            chunks = chunk_markdown(md_file)
            all_chunks.extend(chunks)
            print(f"  [docs] {md_file.name}: {len(chunks)} chunks")

    # 2. Other docs (CODING_CONVENTIONS, etc.)
    docs_dir = AGENT_DIR / "docs"
    for md_file in sorted(docs_dir.glob("*.md")):
        chunks = chunk_markdown(md_file)
        all_chunks.extend(chunks)
        print(f"  [docs] {md_file.name}: {len(chunks)} chunks")

    # 3. Step catalog
    catalog_file = AGENT_DIR / "catalogs" / "step-catalog-en.json"
    if catalog_file.exists():
        chunks = chunk_step_catalog(catalog_file)
        all_chunks.extend(chunks)
        print(f"  [catalog] {len(chunks)} step entries")

    # 4. XML snippet examples
    snippets_dir = AGENT_DIR / "snippet_examples" / "steps"
    if snippets_dir.exists():
        chunks = chunk_xml_snippets(snippets_dir)
        all_chunks.extend(chunks)
        print(f"  [snippets] {len(chunks)} XML files")

    if not all_chunks:
        print("No documents found to index!", file=sys.stderr)
        sys.exit(1)

    print(f"\nTotal chunks to index: {len(all_chunks)}")

    # Create/recreate ChromaDB collection
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    embedding_fn = get_embedding_fn()

    # Delete existing collection if present
    try:
        client.delete_collection(COLLECTION_NAME)
        print("Deleted existing collection.")
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )

    # Add chunks in batches (ChromaDB handles batching internally but let's be explicit)
    batch_size = 50
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i : i + batch_size]
        collection.add(
            ids=[f"chunk_{i + j}" for j in range(len(batch))],
            documents=[c["text"] for c in batch],
            metadatas=[c["metadata"] for c in batch],
        )
        print(f"  Indexed {min(i + batch_size, len(all_chunks))}/{len(all_chunks)} chunks...")

    print(f"\nDone! Index stored at {CHROMA_DIR}")
    print(f"Collection '{COLLECTION_NAME}' has {collection.count()} chunks.")

    # Print summary by type
    type_counts = {}
    for c in all_chunks:
        t = c["metadata"]["type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, count in sorted(type_counts.items()):
        print(f"  {t}: {count}")


if __name__ == "__main__":
    build_index()
