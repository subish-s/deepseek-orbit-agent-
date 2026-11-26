import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from loguru import logger

import agent_core
import deepseek_http
import llm_bridge


load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

logger.add(
    os.path.join(PROJECT_ROOT, "automation.log"),
    rotation="10 MB",
    retention="7 days",
    compression="zip",
    level="INFO",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    deepseek_http.close_client()


app = FastAPI(
    title="DeepSeek HTTP API",
    version="2.0.0",
    lifespan=lifespan,
)


class ChatRequest(BaseModel):
    prompt: str


class AgentRequest(BaseModel):
    prompt: str
    workspace: str | None = None
    max_steps: int = 8


class CompletionMessage(BaseModel):
    role: str = "user"
    content: str | list = ""


class CompletionRequest(BaseModel):
    model: str | None = None
    messages: list[CompletionMessage] = Field(default_factory=list)
    stream: bool = False


security = HTTPBearer()


EXPECTED_TOKEN = os.getenv("API_BEARER_TOKEN", "").strip()
if not EXPECTED_TOKEN:
    raise RuntimeError("API_BEARER_TOKEN is missing from .env")


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    if token != EXPECTED_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing Bearer token")
    return token


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/meta")
async def meta():
    return llm_bridge.describe()


@app.get("/console", response_class=HTMLResponse)
async def console_page():
    path = os.path.join(PROJECT_ROOT, "console", "index.html")
    html = open(path, encoding="utf-8").read()
    html = html.replace('window.DECK_BRIDGE = "proxy"', 'window.DECK_BRIDGE = "direct"', 1)
    return HTMLResponse(html)


@app.post("/chat")
async def chat(data: ChatRequest, token: str = Depends(verify_token)):
    prompt = (data.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty")
    try:
        logger.info(f"Chat request started ({len(prompt)} chars)")
        response = await asyncio.to_thread(llm_bridge.complete, prompt)
        logger.success(f"Chat request completed ({len(response)} chars)")
        return {"success": True, "response": response, "provider": llm_bridge.provider_name()}
    except Exception as exc:
        logger.exception(f"API Error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/agent")
async def agent(data: AgentRequest, token: str = Depends(verify_token)):
    prompt = (data.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty")
    try:
        logger.info(f"Agent request started ({len(prompt)} chars)")
        result = await asyncio.to_thread(agent_core.run_agent, prompt, data.workspace, data.max_steps)
        logger.success(f"Agent request stopped via {result.get('stopped')}")
        return result
    except agent_core.AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Agent error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/v1/models")
async def models(token: str = Depends(verify_token)):
    info = llm_bridge.describe()
    return {"object": "list", "data": [{"id": info["model"], "object": "model", "owned_by": info["provider"]}]}


@app.post("/v1/chat/completions")
async def chat_completions(data: CompletionRequest, token: str = Depends(verify_token)):
    messages = [{"role": item.role, "content": item.content} for item in data.messages]
    if not messages:
        raise HTTPException(status_code=400, detail="messages is empty")
    try:
        text = await asyncio.to_thread(llm_bridge.complete_messages, messages, session_name="openai")
    except Exception as exc:
        logger.exception(f"Completion error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    model = data.model or llm_bridge.describe()["model"]
    if data.stream:
        payload = {
            "id": "chatcmpl-local",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        }
        body = f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n"
        return StreamingResponse(iter([body]), media_type="text/event-stream")
    return {
        "id": "chatcmpl-local",
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
    }
