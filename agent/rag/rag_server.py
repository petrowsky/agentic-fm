#!/usr/bin/env python3
"""RAG server for Agentic FM — retrieves relevant FileMaker documentation chunks."""

from contextlib import asynccontextmanager
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from fastapi import FastAPI
from pydantic import BaseModel

CHROMA_DIR = Path(__file__).resolve().parent / "chroma_db"
COLLECTION_NAME = "agentic_fm"

collection = None


def get_embedding_fn():
    return OllamaEmbeddingFunction(
        url="http://localhost:11434/api/embed",
        model_name="nomic-embed-text",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global collection
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    embedding_fn = get_embedding_fn()
    collection = client.get_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
    )
    count = collection.count()
    print(f"[rag] Loaded collection '{COLLECTION_NAME}' with {count} chunks")
    yield


app = FastAPI(title="Agentic FM RAG Server", lifespan=lifespan)


class QueryRequest(BaseModel):
    query: str
    n_results: int = 5


class QueryResult(BaseModel):
    text: str
    score: float
    metadata: dict


class QueryResponse(BaseModel):
    results: list[QueryResult]


@app.get("/rag/health")
async def health():
    count = collection.count() if collection else 0
    return {"status": "ok", "collection": COLLECTION_NAME, "chunks": count}


@app.post("/rag/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    if not collection:
        return QueryResponse(results=[])

    results = collection.query(
        query_texts=[req.query],
        n_results=req.n_results,
    )

    items = []
    documents = results.get("documents", [[]])[0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    for doc, dist, meta in zip(documents, distances, metadatas):
        # ChromaDB cosine distance: 0 = identical, 2 = opposite
        # Convert to similarity score: 1 - (distance / 2)
        score = 1.0 - (dist / 2.0)
        items.append(QueryResult(text=doc, score=round(score, 4), metadata=meta))
        print(f"  [rag] Retrieved: {meta.get('source', '?')} / {meta.get('section', meta.get('step_name', '?'))} (score={score:.3f})")

    print(f"[rag] Query: {req.query[:80]}... -> {len(items)} results")
    return QueryResponse(results=items)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8081)
