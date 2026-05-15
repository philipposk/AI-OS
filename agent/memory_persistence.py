import os
import json
from typing import Dict, List

class TokenAccounting:
    """Track and report token usage per task."""
    def __init__(self):
        self.total_tokens = 0
        self.task_tokens = {}
    
    def add_tokens(self, count, task_id=None):
        self.total_tokens += count
        if task_id:
            self.task_tokens[task_id] = self.task_tokens.get(task_id,0) + count
    
    def report(self):
        return {"total_tokens": self.total_tokens, "task_tokens": self.task_tokens}

class MemoryPersistence:
    """Persist memory to disk via JSON."""
    def __init__(self, persist_dir="./data/memory"):
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        self.load_memory()
    
    def load_memory(self):
        self.memory_file = self.persist_dir + "/memory_store.json"
        try:
            with open(self.memory_file) as f:
                self._memory = json.load(f)
        except:
            self._memory = []
    
    def add_document(self, text, metadata):
        self._memory.append({"text": text, "metadata": metadata})
        self._save()
    
    def _save(self):
        with open(self.memory_file,"w") as f:
            json.dump(self._memory, f)
    
    def get_relevant_context(self, query, top_k=5):
        query_words = query.lower().split()
        results = []
        for doc in self._memory:
            score = sum(1 for word in query_words if word in doc["text"].lower())
            if score > 0:
                results.append(doc)
        return sorted(results, key=lambda x: -score)[:top_k]

if __name__ == "__main__":
    mp = MemoryPersistence()
    mp.add_document("test", {"test":1})
    docs = mp.get_relevant_context("test")
    for doc in docs[:3]:
        print(doc["text"])