#!/bin/bash
# Deployment script

echo "Deploying GStack AI Company..."

# Install dependencies
pip install -r requirements.txt

# Initialize databases
python -m chromadb Client create --path ./data/chroma

# Start services
echo "Starting orchestrator..."
python ai_company/orchestration.py &

echo "Starting context manager..."
python ai_company/agent/context_manager.py &

echo "Starting memory DB..."
python ai_company/memory/memory_db.py &

echo "GStack AI Company deployed!"