# Devlog

## 2026-03-12 -- Ollama RAG pipeline and /document skill

Added a retrieval-augmented generation (RAG) pipeline so the local Ollama model (qwen2.5-coder:14b) gets relevant FileMaker documentation injected per-query instead of the entire knowledge base crammed into the system prompt. Also created a `/document` skill for auto-capturing development sessions.

### Changes
- New `agent/rag/` directory with ChromaDB-backed indexer and FastAPI query server (port 8081)
- 541 chunks indexed from knowledge docs, step catalog, and XML snippet examples using nomic-embed-text embeddings
- `webviewer/server/ai-proxy.ts` augments Ollama requests with top-5 retrieved chunks (3s timeout, silent fallback)
- New Ollama provider in `webviewer/src/ai/providers/ollama.ts` registered in the provider registry
- `/document` skill at `~/.claude/skills/document/` with CLI and Claude.ai versions

### Notes
- Ollama must be running before starting the RAG server (`open -a Ollama`)
- The `ollama` Python package is a hidden dependency of ChromaDB's OllamaEmbeddingFunction -- install it explicitly
- OllamaEmbeddingFunction URL must be the base URL only (`http://localhost:11434`), not including `/api/embed`
- RAG server is optional -- webviewer works without it, just falls back to unaugmented prompts
- Branch `feature/ollama-rag` committed locally but not yet pushed (git credentials need setup)
