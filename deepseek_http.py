"""Direct HTTP client for the DeepSeek web chat API.

Chat requests go over HTTP. A browser is used only when the saved
account token is missing or rejected, to read the token out of the
existing Chrome profile and then exit.
"""

import base64
import json
import os
import re
import struct
import threading
import time
from datetime import datetime
from typing import Any

import httpx
import wasmtime
from dotenv import load_dotenv
from loguru import logger

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

EMAIL = os.getenv("EMAIL", "")
PASSWORD = os.getenv("PASSWORD", "")
BASE_URL = os.getenv("BASE_URL", "https://chat.deepseek.com/").rstrip("/") + "/"
API_BASE = "https://chat.deepseek.com/api/v0"
WASM_PATH = os.path.join(PROJECT_ROOT, "wasm", "sha3_wasm_bg.wasm")
AUTH_PATH = os.path.join(PROJECT_ROOT, "auth_session.json")
SESSION_PATH = os.path.join(PROJECT_ROOT, "daily_session.json")

MODEL_TYPE = os.getenv("MODEL_TYPE", "expert")
THINKING_ENABLED = os.getenv("THINKING_ENABLED", "true").lower() in ("1", "true", "yes")
SEARCH_ENABLED = os.getenv("SEARCH_ENABLED", "false").lower() in ("1", "true", "yes")

# Login still uses the mobile identity that this host can refresh a token with.
# Chat itself uses the same web client headers the browser sends.
LOGIN_HEADERS = {
    "User-Agent": "DeepSeek/2.5.0 Android/35",
    "X-Client-Version": "2.5.0",
    "X-Client-Platform": "android",
    "X-Client-Locale": "en_US",
    "X-Client-Bundle-Id": "com.deepseek.chat",
    "X-Device-Model": "",
    "X-Client-Timezone-Offset": "0",
    "Origin": "https://chat.deepseek.com",
    "Referer": "https://chat.deepseek.com/",
    "Accept": "application/json",
}

_FRAGMENT_PATH = re.compile(r"^response/fragments/(?P<index>-?\d+)(?:/(?P<rest>.*))?$")
_REFERENCE = re.compile(r"\[reference:\d+\]")
_FENCE = re.compile(r"```[^\n`]*\n[\s\S]*?```")


def _timezone_offset_seconds() -> str:
    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return "0"
    return str(int(offset.total_seconds()))


def _web_headers() -> dict[str, str]:
    return {
        "User-Agent": os.getenv(
            "DEEPSEEK_USER_AGENT",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:153.0) Gecko/20100101 Firefox/153.0",
        ),
        "X-Client-Version": "2.5.0",
        "X-Client-Platform": "web",
        "X-Client-Locale": "en_US",
        "X-Client-Bundle-Id": "com.deepseek.chat",
        "X-Device-Model": "",
        "X-Client-Timezone-Offset": _timezone_offset_seconds(),
        "Origin": "https://chat.deepseek.com",
        "Referer": "https://chat.deepseek.com/",
        "Accept": "*/*",
        "Accept-Language": "en-GB,en;q=0.9",
    }


def _content_path(path: str) -> str:
    if path.endswith("/content"):
        return path
    if re.fullmatch(r"response/fragments/-?\d+", path):
        return path + "/content"
    return "response/fragments/-1/content"


def _strip_references(text: str) -> str:
    """Drop citation markers in prose. Leave text inside fenced code alone."""
    parts: list[str] = []
    last = 0
    for match in _FENCE.finditer(text):
        parts.append(_REFERENCE.sub("", text[last:match.start()]))
        parts.append(match.group(0))
        last = match.end()
    tail = text[last:]
    opened = tail.find("```")
    if opened >= 0:
        parts.append(_REFERENCE.sub("", tail[:opened]))
        parts.append(tail[opened:])
    else:
        parts.append(_REFERENCE.sub("", tail))
    return "".join(parts)


class DeepSeekError(RuntimeError):
    pass


class AuthExpired(DeepSeekError):
    pass


class _PowSolver:
    def __init__(self, wasm_path: str):
        engine = wasmtime.Engine()
        module = wasmtime.Module.from_file(engine, wasm_path)
        self._store = wasmtime.Store(engine)
        instance = wasmtime.Instance(self._store, module, [])
        exports = instance.exports(self._store)
        self._memory = exports["memory"]
        self._add_stack = exports["__wbindgen_add_to_stack_pointer"]
        self._alloc = exports["__wbindgen_export_0"]
        self._solve = exports["wasm_solve"]
        self._lock = threading.Lock()

    def solve(self, challenge: str, salt: str, difficulty: int, expire_at: int) -> int:
        prefix = f"{salt}_{expire_at}_"
        with self._lock:
            retptr = self._add_stack(self._store, -16)
            try:
                challenge_ptr, challenge_len = self._write(challenge)
                prefix_ptr, prefix_len = self._write(prefix)
                self._solve(
                    self._store,
                    retptr,
                    challenge_ptr,
                    challenge_len,
                    prefix_ptr,
                    prefix_len,
                    float(difficulty),
                )
                raw = self._memory.read(self._store, retptr, retptr + 16)
            finally:
                self._add_stack(self._store, 16)
        status = struct.unpack_from("<i", raw, 0)[0]
        value = struct.unpack_from("<d", raw, 8)[0]
        if status == 0:
            raise DeepSeekError("PoW solver returned no answer")
        answer = int(value) if value.is_integer() else value
        return answer

    def _write(self, text: str) -> tuple[int, int]:
        data = text.encode("utf-8")
        ptr = self._alloc(self._store, len(data), 1)
        self._memory.write(self._store, data, ptr)
        return ptr, len(data)


class _DeltaParser:
    """Rebuild one completion from DeepSeek's sticky SSE deltas.

    `o` and `p` persist until the next event sets them. Token text is
    concatenated exactly, including spaces, newlines, and code fences.
    Only RESPONSE fragments are returned. THINK and tool fragments stay out
    of the text the agent and the dashboards display.
    """

    def __init__(self):
        self.op = "SET"
        self.path = ""
        self.fragments: list[dict[str, str]] = []
        self.response_message_id = None
        self.status = None
        self.error = None

    def feed(self, event_name: str, data: str) -> None:
        if not data or data.startswith(":"):
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return

        if event_name == "ready" and isinstance(payload, dict):
            self.response_message_id = payload.get("response_message_id")
            return
        if event_name == "hint" and isinstance(payload, dict):
            if payload.get("type") == "error":
                self.error = payload.get("content") or payload.get("finish_reason") or "stream error"
            return
        if event_name in ("close", "title", "update_session"):
            return
        if not isinstance(payload, dict):
            return
        self._consume(payload)

    def _consume(self, event: dict) -> None:
        had_op = "o" in event
        previous_path = self.path
        if had_op:
            self.op = str(event.get("o") or "SET")
        if "p" in event:
            self.path = str(event.get("p") or "")
        value = event.get("v")
        # A content token after a non-content SET (elapsed time, status) is
        # still the next piece of the answer. Replacing here wipes the code.
        if (
            not had_op
            and isinstance(value, str)
            and self.path.endswith("/content")
            and self.op == "SET"
            and not previous_path.endswith("/content")
        ):
            self.op = "APPEND"
        if self.op == "BATCH" and isinstance(value, list):
            self._apply_batch(self.path, value)
            return
        if self.op == "BATCH" and isinstance(value, str):
            self.op = "APPEND"
            self.path = _content_path(self.path)
            self._apply(self.path, "APPEND", value)
            return
        self._apply(self.path, self.op, value)

    def _apply_batch(self, parent_path: str, items: list) -> None:
        child_op = "SET"
        child_path = ""
        for item in items:
            if not isinstance(item, dict):
                continue
            had_op = "o" in item
            previous_path = child_path
            if had_op:
                child_op = str(item.get("o") or "SET")
            if "p" in item:
                child_path = str(item.get("p") or "")
            value = item.get("v")
            if (
                not had_op
                and isinstance(value, str)
                and child_path == "content"
                and child_op == "SET"
                and previous_path != "content"
            ):
                child_op = "APPEND"
            full = f"{parent_path}/{child_path}" if parent_path and child_path else (parent_path or child_path)
            if child_op == "BATCH" and isinstance(value, list):
                self._apply_batch(full, value)
                continue
            if child_op == "BATCH" and isinstance(value, str):
                self._apply(_content_path(full), "APPEND", value)
                continue
            self._apply(full, child_op, value)

    def _apply(self, path: str, op: str, value: Any) -> None:
        if isinstance(value, dict) and path in ("", "response"):
            response = value.get("response") if path in ("", "response") else None
            if isinstance(response, dict):
                self._take_fragments(response.get("fragments") or [])
                status = response.get("status")
                if isinstance(status, str):
                    self.status = status
                return
        if path == "response/fragments" and op in ("APPEND", "SET"):
            items = value if isinstance(value, list) else [value]
            if op == "SET":
                self.fragments = []
            for item in items:
                if isinstance(item, dict):
                    content = item.get("content")
                    self.fragments.append({
                        "type": str(item.get("type") or "RESPONSE"),
                        "content": content if isinstance(content, str) else "",
                    })
            return
        match = _FRAGMENT_PATH.match(path or "")
        if match and match.group("rest") == "content" and isinstance(value, str):
            fragment = self._fragment_at(match.group("index"))
            if fragment is None:
                return
            if op == "APPEND":
                fragment["content"] += value
            else:
                fragment["content"] = value
            return
        if path == "response/status" and isinstance(value, str):
            self.status = value

    def _fragment_at(self, index_text: str) -> dict[str, str] | None:
        if not self.fragments:
            self.fragments.append({"type": "RESPONSE", "content": ""})
        try:
            index = int(index_text)
        except ValueError:
            return None
        if index < 0:
            index += len(self.fragments)
        if 0 <= index < len(self.fragments):
            return self.fragments[index]
        return None

    def _take_fragments(self, fragments: list) -> None:
        self.fragments = []
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            content = fragment.get("content")
            self.fragments.append({
                "type": str(fragment.get("type") or "RESPONSE"),
                "content": content if isinstance(content, str) else "",
            })

    def text(self) -> str:
        raw = "".join(
            fragment["content"]
            for fragment in self.fragments
            if fragment.get("type") == "RESPONSE"
        )
        return _strip_references(raw).strip()


def _write_private_json(path: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _read_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


class DeepSeekClient:
    def __init__(self):
        self._http = httpx.Client(
            timeout=httpx.Timeout(30.0, read=330.0),
            follow_redirects=False,
            verify=True,
        )
        self._pow = _PowSolver(WASM_PATH)
        self._lock = threading.Lock()
        self._auth = self._load_auth()

    def close(self) -> None:
        self._http.close()

    def complete(
        self,
        prompt: str,
        session_path: str | None = None,
        *,
        thinking_enabled: bool | None = None,
        search_enabled: bool | None = None,
    ) -> str:
        with self._lock:
            try:
                return self._complete(prompt, session_path, thinking_enabled, search_enabled)
            except AuthExpired:
                logger.info("Account token rejected. Signing in again over HTTP.")
                device_id = (self._auth or {}).get("device_id") or self._device_id_from_profile()
                self._auth = self._login_http(device_id)
                return self._complete(prompt, session_path, thinking_enabled, search_enabled)

    def _load_auth(self) -> dict:
        env_token = os.getenv("DEEPSEEK_TOKEN", "").strip()
        if env_token:
            return {
                "token": env_token,
                "device_id": os.getenv("DEEPSEEK_DEVICE_ID", "").strip(),
                "cookie": os.getenv("DEEPSEEK_COOKIE", "").strip(),
            }
        saved = _read_json(AUTH_PATH) or {}
        token = saved.get("token") or ""
        if token:
            return saved
        if saved.get("device_id"):
            return self._login_http(saved["device_id"])
        logger.info("No saved account token. Reading the device id from the browser profile once.")
        return self._login_http(self._device_id_from_profile())

    def _headers(
        self,
        token: str | None = None,
        pow_response: str | None = None,
        stream: bool = False,
        referer: str | None = None,
    ) -> dict:
        headers = _web_headers()
        device_id = (self._auth or {}).get("device_id") or ""
        if device_id:
            headers["X-Device-Id"] = device_id
        cookie = (self._auth or {}).get("cookie") or ""
        if cookie:
            headers["Cookie"] = cookie
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if pow_response:
            headers["X-Ds-Pow-Response"] = pow_response
        if referer:
            headers["Referer"] = referer
        if stream:
            # These are copied from the saved account session. They are not generated here.
            for header, key in (("x-hif-leim", "hif_leim"), ("x-hif-dliq", "hif_dliq")):
                value = str((self._auth or {}).get(key) or "").strip()
                if value:
                    headers[header] = value
        return headers

    def _unwrap(self, response: httpx.Response) -> Any:
        if response.status_code == 202:
            raise DeepSeekError("DeepSeek returned HTTP 202 before the API. The client identity was blocked.")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise DeepSeekError(f"DeepSeek returned a non-JSON response ({response.status_code})") from exc
        return self._unwrap_payload(payload)

    def _unwrap_payload(self, payload: dict) -> Any:
        if payload.get("code") == 40003:
            raise AuthExpired("Account token is no longer valid")
        if payload.get("code") not in (0, None):
            raise DeepSeekError(payload.get("msg") or f"DeepSeek error code {payload.get('code')}")
        data = payload.get("data") or {}
        biz_code = data.get("biz_code") or 0
        if biz_code:
            message = data.get("biz_msg") or f"biz_code {biz_code}"
            if biz_code in (1, 5) or "SESSION" in message or "message id" in message.lower():
                raise DeepSeekError(f"SESSION:{message}")
            raise DeepSeekError(message)
        return data.get("biz_data")

    def _post(self, path: str, body: dict, pow_response: str | None = None, referer: str | None = None) -> Any:
        token = self._auth.get("token")
        if not token:
            raise AuthExpired("Missing account token")
        response = self._http.post(
            f"{API_BASE}{path}",
            headers=self._headers(token, pow_response, referer=referer),
            json=body,
        )
        return self._unwrap(response)

    def _pow_header(self, target_path: str, referer: str | None = None) -> str:
        challenge = self._post("/chat/create_pow_challenge", {"target_path": target_path}, referer=referer)
        challenge = (challenge or {}).get("challenge") or challenge
        if not isinstance(challenge, dict):
            raise DeepSeekError("PoW challenge response was empty")
        started = time.perf_counter()
        answer = self._pow.solve(
            challenge["challenge"],
            challenge["salt"],
            int(challenge["difficulty"]),
            int(challenge["expire_at"]),
        )
        logger.info(f"PoW solved in {(time.perf_counter() - started) * 1000:.0f} ms")
        packed = {
            "algorithm": challenge.get("algorithm") or "DeepSeekHashV1",
            "challenge": challenge["challenge"],
            "salt": challenge["salt"],
            "answer": answer,
            "signature": challenge["signature"],
            "target_path": challenge.get("target_path") or target_path,
        }
        return base64.b64encode(json.dumps(packed, separators=(",", ":")).encode("utf-8")).decode("ascii")

    def _session(self, session_path: str | None = None) -> tuple[str, int | None]:
        path = session_path or SESSION_PATH
        today = time.strftime("%Y-%m-%d")
        saved = _read_json(path) or {}
        session_id = saved.get("chat_session_id")
        if saved.get("date") == today and session_id:
            parent = saved.get("parent_message_id")
            return session_id, parent if isinstance(parent, int) else None

        created = self._post("/chat_session/create", {})
        session_id = ((created or {}).get("chat_session") or {}).get("id")
        if not session_id:
            raise DeepSeekError("Chat session was created without an id")
        self._save_session(session_id, None, path)
        logger.info("Started a new chat session")
        return session_id, None

    def _save_session(self, session_id: str, parent_message_id: int | None, session_path: str | None = None) -> None:
        path = session_path or SESSION_PATH
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        _write_private_json(path, {
            "date": time.strftime("%Y-%m-%d"),
            "chat_session_id": session_id,
            "parent_message_id": parent_message_id,
            "chat_url": f"https://chat.deepseek.com/a/chat/s/{session_id}",
        })

    def _complete(
        self,
        prompt: str,
        session_path: str | None = None,
        thinking_enabled: bool | None = None,
        search_enabled: bool | None = None,
    ) -> str:
        session_id, parent = self._session(session_path)
        try:
            return self._stream(prompt, session_id, parent, session_path, thinking_enabled, search_enabled)
        except DeepSeekError as exc:
            if not str(exc).startswith("SESSION:"):
                raise
            logger.warning(f"Saved chat session was rejected ({exc}). Opening a new one.")
            created = self._post("/chat_session/create", {})
            session_id = ((created or {}).get("chat_session") or {}).get("id")
            if not session_id:
                raise DeepSeekError("Could not replace the rejected chat session") from exc
            self._save_session(session_id, None, session_path)
            return self._stream(prompt, session_id, None, session_path, thinking_enabled, search_enabled)

    def _stream(
        self,
        prompt: str,
        session_id: str,
        parent: int | None,
        session_path: str | None = None,
        thinking_enabled: bool | None = None,
        search_enabled: bool | None = None,
    ) -> str:
        referer = f"https://chat.deepseek.com/a/chat/s/{session_id}"
        pow_response = self._pow_header("/api/v0/chat/completion", referer)
        thinking = THINKING_ENABLED if thinking_enabled is None else bool(thinking_enabled)
        search = SEARCH_ENABLED if search_enabled is None else bool(search_enabled)
        body = {
            "chat_session_id": session_id,
            "parent_message_id": parent,
            "model_type": MODEL_TYPE,
            "prompt": prompt,
            "ref_file_ids": [],
            "thinking_enabled": thinking,
            "search_enabled": search,
            "preempt": False,
        }
        parser = _DeltaParser()
        log_dir = os.path.join(PROJECT_ROOT, "completion_logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"session_{int(time.time())}.txt")

        with self._http.stream(
            "POST",
            f"{API_BASE}/chat/completion",
            headers=self._headers(self._auth.get("token"), pow_response, stream=True, referer=referer),
            json=body,
        ) as response:
            if response.status_code == 202:
                raise DeepSeekError("DeepSeek returned HTTP 202 before the completion stream")
            if response.status_code >= 400:
                detail = response.read().decode("utf-8", "replace")[:300]
                raise DeepSeekError(f"Completion failed with HTTP {response.status_code}: {detail}")
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                raw = response.read()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise DeepSeekError(f"Completion did not return a stream ({response.status_code})") from exc
                self._unwrap_payload(payload)
                raise DeepSeekError("Completion did not return a stream")

            event_name = ""
            data_lines: list[str] = []

            def flush() -> None:
                nonlocal event_name, data_lines
                data = "\n".join(data_lines)
                if data or event_name:
                    if session_path is None:
                        try:
                            with open(log_path, "a", encoding="utf-8") as handle:
                                handle.write(f"event: {event_name}\ndata: {data}\n\n")
                        except OSError:
                            pass
                    parser.feed(event_name, data)
                event_name = ""
                data_lines = []

            for line in response.iter_lines():
                if line == "":
                    flush()
                    if parser.error:
                        break
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            flush()

        if parser.error:
            raise DeepSeekError(parser.error)
        text = parser.text()
        if not text:
            raise DeepSeekError("Completion stream ended without response text")
        if parser.response_message_id is not None:
            try:
                parent_id = int(parser.response_message_id)
            except (TypeError, ValueError):
                parent_id = None
            self._save_session(session_id, parent_id, session_path)
        else:
            self._save_session(session_id, parent, session_path)
        return text

    def _login_http(self, device_id: str) -> dict:
        if not EMAIL or not PASSWORD:
            raise DeepSeekError("EMAIL and PASSWORD are required to refresh the account token")
        if not device_id:
            raise DeepSeekError("Missing device id for account login")
        headers = dict(LOGIN_HEADERS)
        headers["X-Device-Id"] = device_id
        headers["Content-Type"] = "application/json"
        response = self._http.post(
            f"{API_BASE}/users/login",
            headers=headers,
            json={
                "email": EMAIL,
                "mobile": None,
                "password": PASSWORD,
                "area_code": None,
                "device_id": device_id,
                "os": "android",
            },
        )
        biz = self._unwrap(response) or {}
        token = (biz.get("user") or {}).get("token")
        if not token:
            raise DeepSeekError("Login did not return an account token")
        auth = {
            "token": token,
            "device_id": device_id,
            "cookie": "",
            "saved_at": int(time.time()),
        }
        _write_private_json(AUTH_PATH, auth)
        logger.success("Signed in over HTTP and saved the account token")
        return auth

    def _device_id_from_profile(self) -> str:
        device_id = _read_profile_device_id(EMAIL)
        if not device_id:
            raise DeepSeekError("The browser profile has no DeepSeek device id. Open chat.deepseek.com once in that profile.")
        return device_id


def _read_profile_device_id(email: str) -> str:
    """Read the device id the saved Chrome profile already created. Chrome is closed afterward."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    if not email:
        return ""
    profile = os.path.join(PROJECT_ROOT, "cookies", email.split("@")[0])
    os.makedirs(profile, exist_ok=True)
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--no-first-run")
    options.add_argument("--disable-extensions")
    options.add_argument(f"--user-data-dir={profile}")
    driver = webdriver.Chrome(options=options)
    try:
        driver.get(BASE_URL)
        deadline = time.time() + 20
        device_id = ""
        while time.time() < deadline:
            device_id = driver.execute_script(
                "return localStorage.getItem('smidV2') || localStorage.getItem('deepseek-device-id:chat') || '';"
            ) or ""
            if device_id:
                break
            time.sleep(0.4)
        return device_id
    finally:
        driver.quit()


_client: DeepSeekClient | None = None
_client_lock = threading.Lock()


def get_client() -> DeepSeekClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = DeepSeekClient()
        return _client


def close_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def complete(
    prompt: str,
    session_path: str | None = None,
    *,
    thinking_enabled: bool | None = None,
    search_enabled: bool | None = None,
) -> str:
    return get_client().complete(
        prompt,
        session_path,
        thinking_enabled=thinking_enabled,
        search_enabled=search_enabled,
    )
