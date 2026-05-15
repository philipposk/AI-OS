# GStack AI Company

An AI-powered software development company running on orchestrated AI agents.

## Architecture

```
You (Phone/Slack/Browser)
    ↓
Orchestrator (LangGraph)
    ↓
Model Router (select best AI)
    ↓
Workers:
  - Aider (code editing)
  - OpenHands (autonomous tasks)
  - Context Manager (relevant context)
  - Memory DB (long-term knowledge)
    ↓
Communication:
  - Slack
  - Web UI (Streamlit)
```

## Quick Start

```bash
pip install -r requirements.txt
python ai_company/orchestration.py
```

## Components

| Component | Purpose |
|-----------|---------|
| orchestrator.py | Main coordinator |
| models.py | Model selection |
| aider_integration.py | Code editing |
| openhands.py | Autonomous tasks |
| context_manager.py | Context retrieval |
| memory_db.py | Long-term memory |
| slack.py | Slack integration |
| dashboard.py | Web UI |
| main.tf | Infrastructure |

## Setup

1. Clone repo
2. `pip install -r requirements.txt`
3. Configure API keys (OpenAI, Groq, Slack)
4. Run `python ai_company/orchestration.py`

## Tools Used

- LangGraph - Workflow orchestration
- Aider - Code editing hands
- OpenHands - Autonomous agent
- Ollama - Local models
- ChromaDB - Vector memory
- OpenRouter - Model aggregation
- Slack SDK - Communication
- Streamlit - Web dashboard
- Terraform - Infrastructure