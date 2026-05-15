from chromadb import Chroma
from sentence_transformers import SentenceTransformer

# Context Manager - Controls what context is sent to AI
class ContextManager:
    def __init__(self):
        self.vector_db = Chroma(persist_directory="./data/chroma")
        self.embedder = SentenceTransformer('all-MiniLM-L6-v2')
    
    def get_relevant_context(self, query: str, top_k: int = 5):
        """Find most relevant code/docs for query"""
        query_embedding = self.embedder.encode(query)
        results = self.vector_db.similarity_search_by_vector(
            query_embedding, n_results=top_k
        )
        return results
    
    def add_document(self, text: str, metadata: dict = None):
        """Add a document to context database"""
        doc_embedding = self.embedder.encode(text)
        self.vector_db.add(
            documents=[text],
            embeddings=[doc_embedding],
            metadatas=[metadata]
        )
    
    def clear_context(self):
        """Clear all context"""
        self.vector_db.delete_collection()

if __name__ == "__main__":
    cm = ContextManager()
    cm.add_document("test code for dark mode", {"source": "app.py"})
    print("Context manager initialized")