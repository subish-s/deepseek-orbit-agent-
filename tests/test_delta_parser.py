"""The completion parser must rebuild every fenced language exactly.

The dashboards and the agent both display this text. Thinking, search, and
citation markers stay out of it. Spaces, newlines, and indentation inside
code fences stay in it.
"""

import json
import re
import unittest

from deepseek_http import _DeltaParser

PYTHON = "def add(a, b):\n    return a + b\n"
JAVASCRIPT = 'function greet(name) {\n  const marker = "[reference:1]";\n  return `hi ${name}`;\n}\n'
SWIFT = (
    "import WidgetKit\n"
    "import SwiftUI\n\n"
    "struct SimpleEntry: TimelineEntry {\n"
    "    let date: Date\n"
    "    let message: String\n"
    "}\n"
)
SQL = "SELECT id\nFROM widgets\nWHERE free = 1;\n"
HTML = '<div class="card">\n  <span>Hello</span>\n</div>\n'
RUST = 'fn main() {\n    println!("ok");\n}\n'
GO = 'package main\n\nimport "fmt"\n\nfunc main() {\n    fmt.Println("ok")\n}\n'
BASH = 'echo "$HOME"\n'
JSON_CODE = '{"action":"done","summary":"ok"}\n'
CPP = "std::vector<int> nums{1, 2};\n"

BLOCKS = {
    "python": PYTHON,
    "javascript": JAVASCRIPT,
    "swift": SWIFT,
    "sql": SQL,
    "html": HTML,
    "rust": RUST,
    "go": GO,
    "bash": BASH,
    "json": JSON_CODE,
    "cpp": CPP,
}


def _event(payload, name=""):
    line = "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if name:
        return f"event: {name}\n{line}\n\n"
    return line + "\n\n"


def _chunks(text, size=6):
    for index in range(0, len(text), size):
        yield text[index:index + size]


def _feed(parser, sse):
    event_name = ""
    data_lines = []

    def flush():
        nonlocal event_name, data_lines
        if data_lines or event_name:
            parser.feed(event_name, "\n".join(data_lines))
        event_name = ""
        data_lines = []

    for line in sse.splitlines():
        if line == "":
            flush()
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush()
    return parser.text()


def _answer_with_fences():
    parts = ["Yes, you can use these.\n\n"]
    for lang, code in BLOCKS.items():
        parts.append(f"```{lang}\n{code}```\n\n")
    return "".join(parts).strip()


def _stream_for(answer):
    sse = [
        _event({"request_message_id": 1, "response_message_id": 2, "model_type": "default"}, "ready"),
        _event({"updated_at": 1}, "update_session"),
        _event({"v": {"response": {
            "status": "WIP",
            "fragments": [{"id": 2, "type": "THINK", "content": "The"}],
        }}}),
        _event({"p": "response/fragments/-1/content", "o": "APPEND", "v": " user"}),
        _event({"v": " is asking"}),
        _event({"p": "response/fragments/-1/elapsed_secs", "o": "SET", "v": 0.8}),
        _event({"p": "response", "o": "BATCH", "v": [
            {"p": "fragments", "o": "APPEND", "v": [{"id": 3, "type": "TOOL_SEARCH", "content": None}]},
            {"p": "has_pending_fragment", "o": "SET", "v": False},
        ]}),
        _event({"p": "response/fragments/-1", "o": "BATCH", "v": [
            {"p": "status", "v": "FINISHED"},
            {"p": "content", "v": "Found 34 web pages"},
        ]}),
        _event({"p": "response/fragments", "o": "APPEND", "v": [
            {"id": 11, "type": "RESPONSE", "content": ""},
        ]}),
    ]
    # Citation lands in the prose, then the next token omits p and o.
    prose, _, rest = answer.partition("\n\n")
    body = prose + "[reference:0]"
    first, *more = list(_chunks(body, 7))
    sse.append(_event({"p": "response/fragments/-1/content", "o": "APPEND", "v": first}))
    for piece in more:
        sse.append(_event({"v": piece}))
    sse.append(_event({"p": "response/fragments/-1", "o": "BATCH", "v": [
        {"p": "content", "o": "APPEND", "v": ""},
        {"p": "references", "o": "SET", "v": [{"id": 6, "type": "TOOL_OPEN"}]},
    ]}))
    # Inherited BATCH must not eat the fence that follows.
    code = "\n\n" + rest
    cut = code.find("    return a + b\n")
    head, tail = code[:cut], code[cut:]
    for piece in _chunks(head, 5):
        sse.append(_event({"v": piece}))
    sse.append(_event({"p": "response/fragments/-1/elapsed_secs", "o": "SET", "v": 1.2}))
    indented, *after = list(_chunks(tail, 5))
    sse.append(_event({"p": "response/fragments/-1/content", "v": indented}))
    for piece in after:
        sse.append(_event({"v": piece}))
    sse.append(_event({"p": "response", "o": "BATCH", "v": [
        {"p": "accumulated_token_usage", "v": 10},
        {"p": "quasi_status", "v": "FINISHED"},
    ]}))
    sse.append(_event({"p": "response/status", "o": "SET", "v": "FINISHED"}))
    sse.append(_event({"content": "Code samples"}, "title"))
    sse.append(_event({"click_behavior": "none", "auto_resume": False}, "close"))
    return "".join(sse)


class DeltaParserTests(unittest.TestCase):
    def test_every_language_fence_is_exact(self):
        expected = _answer_with_fences()
        text = _feed(_DeltaParser(), _stream_for(expected))
        self.assertEqual(text, expected)
        self.assertNotIn("The user is asking", text)
        self.assertNotIn("Found 34 web pages", text)
        self.assertNotIn("[reference:0]", text)
        self.assertIn('const marker = "[reference:1]"', text)

        found = {}
        for match in re.finditer(r"```([^\n`]*)\n([\s\S]*?)```", text):
            found[match.group(1)] = match.group(2)
        self.assertEqual(set(found), set(BLOCKS))
        for lang, code in BLOCKS.items():
            self.assertEqual(found[lang], code, lang)

    def test_swift_widget_sample_shape(self):
        code = (
            "import WidgetKit\n"
            "import SwiftUI\n\n"
            "struct SimpleEntry: TimelineEntry {\n"
            "    let date: Date\n"
            "}\n"
        )
        answer = "Assuming you mean iOS widget code.\n\n```swift\n" + code + "```"
        sse = [
            _event({"v": {"response": {"fragments": [{"type": "THINK", "content": "We"}]}}}),
            _event({"p": "response/fragments/-1/content", "o": "APPEND", "v": " need answer."}),
            _event({"p": "response/fragments", "o": "APPEND", "v": [
                {"type": "RESPONSE", "content": "Assuming"},
            ]}),
            _event({"p": "response/fragments/-1/content", "v": " you mean"}),
        ]
        for piece in _chunks(" iOS widget code.\n\n```sw", 4):
            sse.append(_event({"v": piece}))
        sse.append(_event({"v": "ift"}))
        sse.append(_event({"v": "\n" + code + "```"}))
        text = _feed(_DeltaParser(), "".join(sse))
        self.assertEqual(text, answer)
        self.assertNotIn("We need answer", text)
        self.assertIn("struct SimpleEntry: TimelineEntry", text)

    def test_agent_json_fence_stays_parseable(self):
        import agent_core

        raw = '```json\n{"action":"read","path":"add.py"}\n```'
        sse = _event({"p": "response/fragments", "o": "APPEND", "v": [
            {"type": "THINK", "content": "We should answer with JSON {\"action\":\"run\"}."},
        ]})
        sse += _event({"p": "response/fragments", "o": "APPEND", "v": [
            {"type": "RESPONSE", "content": raw},
        ]})
        text = _feed(_DeltaParser(), sse)
        action = agent_core.parse_action(text)
        self.assertEqual(action["action"], "read")
        self.assertEqual(action["path"], "add.py")


if __name__ == "__main__":
    unittest.main()
