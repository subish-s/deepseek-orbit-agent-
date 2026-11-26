# DeepSeek API & Orbit Autonomous AI Agent

A high-performance DeepSeek integration featuring a direct HTTP client with WebAssembly PoW acceleration, an OpenAI-compatible FastAPI backend, an interactive web console, and **Orbit Agent** (`agent_core.py`) — an autonomous local AI coding agent that can inspect, patch, run, and debug code within a project workspace.

---

## Architecture Overview

1. **Autonomous AI Coding Agent (`agent_core.py`)**:
   CLI and programmatic autonomous coding loop that analyzes projects, reads files, applies targeted patches, executes tests or scripts, inspects output/errors, and iterates until the goal is achieved.
2. **Main API & Server (`app.py`)**:
   FastAPI service exposing:
   - `/agent`: Autonomous AI agent execution endpoint.
   - `/chat`: Direct prompt completions.
   - `/v1/chat/completions`: OpenAI-compatible completions (streaming and non-streaming).
   - `/console`: Built-in web UI ("Orbit Deck") for interactive chat and agent tasks.
3. **LLM Bridge & Direct HTTP Engine (`llm_bridge.py` & `deepseek_http.py`)**:
   Direct HTTP transport with local WebAssembly (`wasm/sha3_wasm_bg.wasm`) proof-of-work computation, session sharing (`daily_session.json`), and automatic token management.

---

## Requirements & Setup

### 1. Prerequisites
- Python `3.10` or newer (Python 3.12 recommended).
- Google Chrome (used only for initial/fallback session authentication if needed).

### 2. Installation
```bash
# Clone and enter the repository directory
cd deep-seek-api

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration (`.env`)
Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Key variables:
```ini
EMAIL=your_email@example.com
PASSWORD=your_deepseek_password
BASE_URL=https://chat.deepseek.com/
API_BEARER_TOKEN=your_secure_bearer_token_here
AGENT_ROOT=/path/to/your/workspace
```

- `EMAIL` & `PASSWORD`: Your DeepSeek credentials (used for automated session token refresh).
- `API_BEARER_TOKEN`: Bearer token securing HTTP API endpoints (generate one with `python3 bearer.py` or `openssl rand -hex 32`).
- `AGENT_ROOT`: (Optional) Root directory that the AI agent is allowed to access and operate in.

---

## How to Run the Main AI Script

> [!IMPORTANT]
> Always execute these commands from the **root project directory** (`deep-seek-api/`), not inside subfolders like `dashboard-node/`.
> ```bash
> cd deep-seek-api
> ```

### Method 1: Run the Autonomous AI Agent via CLI (`agent_core.py`)

The primary autonomous coding agent script is `agent_core.py`. Run it from the project root inside your activated virtual environment:

```bash
# 1. Ensure you are in the project root
cd deep-seek-api

# 2. Activate the virtual environment
source venv/bin/activate

# 3. Run the agent with a goal
python3 agent_core.py "Fix bug in calculate_total function and run tests"
```

#### CLI Options & Flags:
```bash
python3 agent_core.py "<goal>" [--workspace <path>] [--steps <max_steps>]
```
- `<goal>`: **(Required)** Natural language description of what you want the AI agent to accomplish.
- `--workspace <path>`: Target directory for the agent to inspect and edit (must be within `AGENT_ROOT`, defaults to current directory).
- `--steps <number>`: Maximum number of iteration steps allowed (default: `8`, hard cap: `12`).

#### Examples:
```bash
# Run agent on a specific subfolder/workspace
python3 agent_core.py "Implement unit tests for user authentication" --workspace /path/to/my-project

# Run with custom step limit
python3 agent_core.py "Refactor database query logic in repository.py" --steps 10
```

---

### Method 2: Interactive Terminal Chat (`chat.py`)

To chat with DeepSeek directly in your terminal:

```bash
# 1. Ensure you are in the project root and activate virtualenv
cd deep-seek-api
source venv/bin/activate

# 2. Launch interactive multi-turn chat
python3 chat.py

# Or send a single one-off prompt:
python3 chat.py "What is the difference between processes and threads?"
```

---

### Method 3: Run the Main AI API Server & Web UI (`app.py`)

To serve the AI agent and chat endpoints over HTTP, start the FastAPI server:

```bash
# 1. Activate the virtual environment
source venv/bin/activate

# 2. Start the FastAPI server on port 8000
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Once running:
- **Interactive Web Console ("Orbit Deck")**: Open `http://127.0.0.1:8000/console` in your browser to interact with the AI in Chat or Agent mode.
- **Health Check**: `http://127.0.0.1:8000/health`
- **Model Info**: `http://127.0.0.1:8000/meta`

---

## API Endpoints & Usage

All protected endpoints require the `Authorization: Bearer <API_BEARER_TOKEN>` header.

### 1. Run AI Agent via API (`POST /agent`)

Trigger an autonomous agent run over HTTP:

```bash
curl -X POST http://127.0.0.1:8000/agent \
  -H "Authorization: Bearer YOUR_API_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Inspect test_delta_parser.py, find failing tests, and fix them",
    "workspace": "/path/to/your/workspace",
    "max_steps": 8
  }'
```

### 2. Standard Chat Completion (`POST /chat`)

Send a direct prompt to the DeepSeek model:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Authorization: Bearer YOUR_API_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a Python function for quicksort and explain its time complexity."}'
```

### 3. OpenAI-Compatible Chat (`POST /v1/chat/completions`)

Compatible with OpenAI SDKs and third-party tools:

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "account-chat",
    "messages": [
      {"role": "user", "content": "Explain asynchronous programming in Python"}
    ],
    "stream": false
  }'
```

### 4. Python Example

```python
import requests

url = "http://127.0.0.1:8000/chat"
headers = {
    "Authorization": "Bearer YOUR_API_BEARER_TOKEN",
    "Content-Type": "application/json"
}
payload = {
    "prompt": "Explain the difference between TCP and UDP clearly."
}

response = requests.post(url, json=payload, headers=headers, timeout=300)
print(response.json())
```

---

## Running the Automated Test Suite

To verify agent core behaviors, security guardrails, and parsing logic:

```bash
source venv/bin/activate
python3 -m unittest discover tests
```

---

## Project Structure

```
.
├── agent_core.py               # Main autonomous AI coding agent (CLI & module)
├── app.py                      # Main FastAPI server (/agent, /chat, /v1, /console)
├── llm_bridge.py               # Unified LLM completion layer and session mapper
├── deepseek_http.py            # Direct HTTP client with WASM PoW solver
├── console/
│   └── index.html              # Orbit Deck web UI for chat & agent tasks
├── wasm/
│   └── sha3_wasm_bg.wasm       # WebAssembly engine for DeepSeek proof-of-work
├── tests/                      # Unit test suite
├── daily_session.json          # Shared active session state
├── auth_session.json           # Cached authentication tokens
├── requirements.txt            # Python dependencies
└── README.md                   # Project documentation
```

---

## License

This project is licensed under the [MIT License](LICENSE).
