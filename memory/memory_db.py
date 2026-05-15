# Memory Database - Long-term memory for the system
import redis
import json
from chromadb import Chroma
from sentence_transformers import SentenceTransformer

class MemoryDB:
    def __init__(self):
        self.redis = redis.Redis(host='localhost', port=6379, db=0)
        self.vector_db = Chroma(persist_directory="./data/memory")
        self.embedder = SentenceTransformer('all-MiniLM-L6-v2')
    
    def store_conversation(self, role: str, message: str):
        """Store conversation in memory"""
        key = f"conv:{role}:{len(self.redis.keys('conv:*'))}"
        self.redis.set(key, json.dumps({"role": role, "message": message}))
    
    def store_project_memory(self, text: str, metadata: dict = None):
        """Store project knowledge"""
        embedding = self.embedder.encode(text)
        self.vector_db.add(
            documents=[text],
            embeddings=[embedding],
            metadatas=[metadata]
        )
    
    def retrieve_relevant(self, query: str, top_k: int = 3):
        """Retrieve relevant memories"""
        query_embedding = self.embedder.encode(query)
        results = self.vector_db.similarity_search_by_vector(
            query_embedding, n_results=top_k
        )
        return results

if __name__ == "__main__":
    print("Memory DB module loaded")