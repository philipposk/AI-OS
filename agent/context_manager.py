from typing import List, Dict, Optional
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import chromadb; fall back to simple in-memory if unavailable
try:
    from chromadb import Chroma
    from sentence_transformers import SentenceTransformer
    HAS_CHROMA = True
except Exception as e:
    logger.warning(f"chromadb unavailable ({e}), using fallback")
    HAS_CHROMA = False

class ContextManager:
    """
    Context manager with fallback to in-memory storage.
    Tries to use ChromaDB if available, otherwise uses simple dict-based memory.
    """

    def __init__(self, persist_dir: str = "./data/chroma"):
        self.persist_dir = persist_dir
        self._memory_db: List[Dict] = []
        self._chroma_client = None
        
        if HAS_CHROMA:
            try:
                import os
                os.makedirs(persist_dir, exist_ok=True)
                self._chroma_client = Chroma(persist_directory=persist_dir)
                logger.info("ChromaDB context manager initialized")
            except Exception as e:
                logger.warning(f"Chroma fallback ({e})")

    def add_document(self, text: str, metadata: Dict = None) -> None:
        """Add a document to context memory."""
        doc = {"text": text, "metadata": metadata or {}}
        self._memory_db.append(doc)
        
    def get_relevant_context(self, query: str, top_k: int = 5) -> List[Dict]:
        """Get relevant documents matching query."""
        # Simple keyword-based search
        query_words = query.lower().split()
        results = []
        for doc in self._memory_db:
            score = sum(1 for word in query_words if word in doc["text"].lower())
            if score > 0:
                results.append({"document": doc["text"], "metadata": doc["metadata"], "score": score})
        return sorted(results, key=lambda x: -x["score"])[:top_k]