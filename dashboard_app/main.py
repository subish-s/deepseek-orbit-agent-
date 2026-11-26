import os
import re
import time
import json
import asyncio
import urllib.request
import urllib.parse
import urllib.error
from fastapi import FastAPI, HTTPException, Header, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================
# DASHBOARD CONFIGURATION (from .env)
# ============================================

# Admin credentials (REQUIRED - set in .env)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_PATH = os.getenv("ADMIN_PATH", "/admin").rstrip("/")

# Optional user authentication for the main chat
REQUIRE_USER_AUTH = os.getenv("REQUIRE_USER_AUTH", "false").lower() in ("true", "1", "yes")
USER_USERNAME = os.getenv("USER_USERNAME", "user")
USER_PASSWORD = os.getenv("USER_PASSWORD", "")

# Security note
if not ADMIN_PASSWORD:
    print("⚠️  WARNING: ADMIN_PASSWORD is not set in .env. Admin panel is insecure!")

print(f"[DASHBOARD] User auth required for chat: {REQUIRE_USER_AUTH} (USER_USERNAME={USER_USERNAME!r})")

app = FastAPI(
    title="Advanced DeepSeek API Dashboard",
    version="2.0.0"
)

@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    return Response(status_code=204)

@app.get("/console")
async def console_page():
    return FileResponse(os.path.join(PROJECT_ROOT, "console", "index.html"))

@app.get("/api/meta")
async def api_meta():
    return await _proxy_json("GET", "/meta", None)

# In-memory history for proxy requests
request_history = []

class ChatRequest(BaseModel):
    prompt: str

class AgentRequest(BaseModel):
    prompt: str
    workspace: str | None = None
    max_steps: int = 8

# Enhanced HTML with advanced code block rendering
USER_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DeepSeek AI - Advanced Console</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Syne:wght@400;600;700&family=Space+Grotesk:wght@400;600;700&display=swap" rel="stylesheet">
    <!-- Markdown and Code Highlighting -->
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/styles/atom-one-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/highlight.min.js"></script>
    <!-- AUTH_CONFIG_INJECTION -->
    <style>
        :root {
            --bg-primary: #0a0e17;
            --bg-secondary: #1a1f2e;
            --bg-tertiary: rgba(30, 40, 60, 0.4);
            --border-color: rgba(100, 150, 255, 0.1);
            --border-glow: rgba(100, 150, 255, 0.3);
            --accent-blue: #6496ff;
            --accent-cyan: #00d9ff;
            --accent-purple: #8b5cf6;
            --success: #10b981;
            --danger: #ef4444;
            --text-primary: #e5e7eb;
            --text-secondary: #9ca3af;
            --code-bg: #0f1419;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Space Grotesk', sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            height: 100vh;
            display: flex;
            overflow: hidden;
            background-image: 
                radial-gradient(circle at 20% 50%, rgba(100, 150, 255, 0.1) 0%, transparent 50%),
                radial-gradient(circle at 80% 80%, rgba(139, 92, 246, 0.08) 0%, transparent 50%);
            background-attachment: fixed;
        }

        /* Sidebar */
        .sidebar {
            width: 320px;
            background: linear-gradient(180deg, #0f1419 0%, #1a1f2e 100%);
            border-right: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            padding: 32px 24px;
            backdrop-filter: blur(20px);
            z-index: 10;
            overflow-y: auto;
        }

        .logo-area {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 48px;
        }

        .logo-icon {
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
            border-radius: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Syne', sans-serif;
            font-weight: 700;
            color: white;
            font-size: 24px;
            box-shadow: 0 8px 24px rgba(100, 150, 255, 0.3);
        }

        .logo-title {
            font-family: 'Syne', sans-serif;
            font-weight: 700;
            font-size: 24px;
            color: white;
            letter-spacing: -0.5px;
        }

        .sidebar-section-title {
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: var(--accent-blue);
            margin-bottom: 16px;
            font-weight: 700;
        }

        .session-card {
            background: rgba(30, 40, 60, 0.4);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 24px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .session-card:hover {
            border-color: var(--border-glow);
            background: rgba(30, 40, 60, 0.6);
            transform: translateY(-2px);
        }

        .session-meta {
            margin-bottom: 14px;
        }

        .session-meta:last-child {
            margin-bottom: 0;
        }

        .session-label {
            font-size: 9px;
            text-transform: uppercase;
            color: var(--text-secondary);
            margin-bottom: 6px;
            font-weight: 600;
            letter-spacing: 1px;
        }

        .session-value {
            font-size: 14px;
            color: white;
            font-weight: 600;
        }

        .sidebar-footer {
            margin-top: auto;
            border-top: 1px solid var(--border-color);
            padding-top: 20px;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .admin-link {
            color: var(--accent-cyan);
            text-decoration: none;
            font-size: 13px;
            font-weight: 600;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border-radius: 8px;
            background: rgba(0, 217, 255, 0.08);
            border: 1px solid rgba(0, 217, 255, 0.2);
        }

        .admin-link:hover {
            background: rgba(0, 217, 255, 0.15);
            border-color: rgba(0, 217, 255, 0.4);
            text-shadow: 0 0 8px rgba(0, 217, 255, 0.4);
        }

        /* Chat Container */
        .chat-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            height: 100%;
            position: relative;
        }

        .chat-header {
            height: 80px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 48px;
            backdrop-filter: blur(20px);
            background: rgba(10, 14, 23, 0.5);
        }

        .chat-header-title {
            font-family: 'Syne', sans-serif;
            font-size: 20px;
            font-weight: 700;
            color: white;
            letter-spacing: -0.5px;
        }

        .header-login-btn:hover {
            background: rgba(100,150,255,0.2);
            border-color: rgba(100,150,255,0.5);
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(16, 185, 129, 0.12);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: var(--success);
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 12px var(--success);
            animation: pulse 2s infinite;
        }

        .chat-history {
            flex: 1;
            overflow-y: auto;
            padding: 48px;
            display: flex;
            flex-direction: column;
            gap: 28px;
        }

        .message-row {
            display: flex;
            width: 100%;
            animation: messageSlide 0.3s ease-out;
        }

        @keyframes messageSlide {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .message-row.user {
            justify-content: flex-end;
        }

        .message-row.bot {
            justify-content: flex-start;
        }

        .message-bubble {
            max-width: 75%;
            padding: 20px 24px;
            border-radius: 16px;
            font-size: 15px;
            line-height: 1.7;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        }

        .message-row.user .message-bubble {
            background: linear-gradient(135deg, #1a3a52, #2563eb);
            color: white;
            border: 1px solid rgba(37, 99, 235, 0.3);
            border-bottom-right-radius: 4px;
        }

        .message-row.bot .message-bubble {
            background: var(--bg-secondary);
            color: var(--text-primary);
            border: 1px solid var(--border-color);
            border-bottom-left-radius: 4px;
            backdrop-filter: blur(10px);
        }

        /* Advanced Markdown Styling */
        .message-bubble h1 {
            font-family: 'Syne', sans-serif;
            font-size: 24px;
            font-weight: 700;
            color: white;
            margin: 26px 0 14px 0;
            letter-spacing: -0.5px;
        }

        .message-bubble h2 {
            font-family: 'Syne', sans-serif;
            font-size: 19px;
            font-weight: 700;
            color: white;
            margin: 26px 0 10px 0;
            padding-bottom: 8px;
            border-bottom: 2px solid rgba(100, 150, 255, 0.35);
            letter-spacing: -0.3px;
        }

        .message-bubble h3 {
            font-size: 16px;
            font-weight: 700;
            color: var(--accent-cyan);
            margin: 20px 0 8px 0;
            letter-spacing: -0.1px;
        }

        .message-bubble h4 {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-secondary);
            margin: 14px 0 6px 0;
        }
        
        /* Premium explanation sections (Time Complexity, Edge Cases, etc.) */
        .message-bubble h2 + p,
        .message-bubble h3 + p {
            color: var(--text-primary);
            font-size: 14.5px;
            line-height: 1.75;
        }

        .message-bubble p {
            margin-bottom: 14px;
            line-height: 1.8;
            color: var(--text-primary);
        }

        .message-bubble p:last-child {
            margin-bottom: 0;
        }

        .message-bubble strong {
            color: white;
            font-weight: 700;
        }

        .message-bubble em {
            color: var(--accent-cyan);
            font-style: italic;
        }

        .message-bubble ul, .message-bubble ol {
            padding-left: 26px;
            margin: 12px 0 18px;
        }

        .message-bubble li {
            margin-bottom: 7px;
            line-height: 1.75;
            font-size: 14.5px;
        }

        .message-bubble li::marker {
            color: var(--accent-blue);
            font-weight: 700;
        }
        
        /* Beautiful bullet styling for feature lists */
        .message-bubble ul li {
            position: relative;
        }

        .message-bubble blockquote {
            border-left: 4px solid var(--accent-blue);
            padding: 12px 18px;
            margin: 16px 0;
            background: rgba(100, 150, 255, 0.08);
            border-radius: 0 8px 8px 0;
            color: var(--text-secondary);
            font-style: italic;
        }

        .message-bubble hr {
            border: none;
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--border-color), transparent);
            margin: 24px 0;
        }

        /* Inline Code */
        .message-bubble code:not(pre code) {
            font-family: 'JetBrains Mono', monospace;
            background: rgba(100, 150, 255, 0.12);
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 0.9em;
            color: var(--accent-cyan);
            border: 1px solid rgba(100, 150, 255, 0.2);
            font-weight: 500;
        }

        /* === Premium ChatGPT-style Response Header === */
        .response-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 12px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
        }
        
        .response-header-left {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .ai-label {
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            color: var(--accent-cyan);
            background: rgba(0, 217, 255, 0.1);
            padding: 3px 10px;
            border-radius: 999px;
            border: 1px solid rgba(0, 217, 255, 0.2);
        }
        
        .response-copy-btn {
            display: flex;
            align-items: center;
            gap: 6px;
            background: rgba(100, 150, 255, 0.08);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 6px 12px;
            color: var(--accent-blue);
            font-size: 11px;
            font-weight: 600;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .response-copy-btn:hover {
            background: rgba(100, 150, 255, 0.15);
            border-color: var(--border-glow);
        }
        
        .response-copy-btn.copied {
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
            border-color: rgba(16, 185, 129, 0.3);
        }
        
        .response-copy-btn svg {
            flex-shrink: 0;
        }

        /* Quick Action Buttons (Regenerate etc.) */
        .quick-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 14px;
            padding-top: 10px;
            border-top: 1px dashed var(--border-color);
        }
        
        .quick-btn {
            font-size: 12px;
            padding: 5px 11px;
            border-radius: 999px;
            background: rgba(100, 150, 255, 0.08);
            border: 1px solid var(--border-color);
            color: var(--accent-blue);
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: all 0.15s;
            font-weight: 500;
        }
        
        .quick-btn:hover {
            background: rgba(100, 150, 255, 0.18);
            transform: translateY(-1px);
        }

        /* Code Block Container - Premium ChatGPT style */
        .code-container {
            background: var(--code-bg);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            margin: 18px 0;
            overflow: hidden;
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.45);
        }

        .code-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 11px 16px;
            background: rgba(25, 32, 48, 0.9);
            border-bottom: 1px solid var(--border-color);
        }

        .code-language {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 12px;
            font-weight: 600;
            color: var(--accent-blue);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .code-lang-icon {
            width: 20px;
            height: 20px;
            border-radius: 5px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            font-weight: 700;
            color: white;
        }

        .code-lang-icon.python { background: linear-gradient(135deg, #366994, #ffd43b); }
        .code-lang-icon.javascript { background: linear-gradient(135deg, #f0db4f, #e34c26); }
        .code-lang-icon.json { background: linear-gradient(135deg, #5b9a4d, #90c53f); }
        .code-lang-icon.bash { background: #1e1e1e; border: 1px solid #666; }
        .code-lang-icon.html { background: #e34c26; }
        .code-lang-icon.css { background: #264de4; }
        .code-lang-icon.sql { background: #e38c00; }
        .code-lang-icon.default { background: #555; }

        .code-actions {
            display: flex;
            gap: 10px;
            align-items: center;
        }

        .code-btn {
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 6px;
            background: rgba(100, 150, 255, 0.1);
            border: 1px solid var(--border-color);
            border-radius: 7px;
            padding: 6px 12px;
            color: var(--accent-blue);
            font-size: 11px;
            font-weight: 600;
            font-family: inherit;
            transition: all 0.2s;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .code-btn:hover {
            background: rgba(100, 150, 255, 0.2);
            border-color: var(--border-glow);
            box-shadow: 0 0 12px rgba(100, 150, 255, 0.15);
        }

        .code-btn.copied {
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
            border-color: rgba(16, 185, 129, 0.3);
        }

        .code-btn.copied:hover {
            box-shadow: 0 0 12px rgba(16, 185, 129, 0.15);
        }
        
        /* Grok-style icon-only code buttons */
        .code-btn.icon-only {
            padding: 6px;
            width: 30px;
            height: 30px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: transparent;
            border: 1px solid transparent;
        }
        
        .code-btn.icon-only:hover {
            background: rgba(100, 150, 255, 0.15);
            border-color: var(--border-glow);
        }
        
        .code-btn.icon-only.copied {
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
            border-color: rgba(16, 185, 129, 0.3);
        }
        
        /* Extra polish for code header labels */
        .code-language span {
            font-weight: 600;
            letter-spacing: 0.3px;
        }

        .code-content {
            position: relative;
            overflow-x: auto;
        }

        .message-bubble pre {
            margin: 0;
            padding: 18px;
            background: transparent;
            border: none;
            overflow-x: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            line-height: 1.6;
        }

        .message-bubble pre code {
            background: transparent;
            padding: 0;
            color: #e5e7eb;
            font-size: inherit;
            font-weight: 400;
        }

        .message-bubble table {
            width: 100%;
            border-collapse: collapse;
            margin: 18px 0;
            font-size: 13px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
        }

        .message-bubble th, .message-bubble td {
            border: 1px solid var(--border-color);
            padding: 12px 16px;
            text-align: left;
        }

        .message-bubble th {
            background: rgba(100, 150, 255, 0.1);
            color: var(--accent-blue);
            font-weight: 700;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .message-bubble tr:nth-child(even) td {
            background: rgba(100, 150, 255, 0.04);
        }

        /* Input Area */
        .chat-input-area {
            padding: 36px 48px;
            backdrop-filter: blur(20px);
            background: rgba(10, 14, 23, 0.5);
            border-top: 1px solid var(--border-color);
        }

        .input-wrapper {
            position: relative;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 28px;
            padding: 10px 18px;
            display: flex;
            align-items: center;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        }

        .input-wrapper:focus-within {
            border-color: var(--border-glow);
            box-shadow: 0 0 20px rgba(100, 150, 255, 0.2), 0 4px 12px rgba(0, 0, 0, 0.2);
            background: linear-gradient(135deg, rgba(30, 40, 60, 0.6), rgba(40, 50, 80, 0.4));
        }

        .chat-input {
            flex: 1;
            background: transparent;
            border: none;
            outline: none;
            color: white;
            font-family: inherit;
            font-size: 15px;
            padding: 14px 8px;
            resize: none;
            height: 52px;
            max-height: 140px;
            font-weight: 500;
        }

        .chat-input::placeholder {
            color: var(--text-secondary);
        }

        .send-btn {
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
            border: none;
            outline: none;
            width: 48px;
            height: 48px;
            border-radius: 50%;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
            box-shadow: 0 4px 15px rgba(100, 150, 255, 0.3);
            flex-shrink: 0;
        }

        .send-btn:hover {
            transform: scale(1.08);
            box-shadow: 0 6px 24px rgba(100, 150, 255, 0.4);
        }

        .send-btn:active {
            transform: scale(0.95);
        }

        .send-btn svg {
            width: 20px;
            height: 20px;
            fill: white;
            transform: rotate(45deg);
        }

        /* Typing Indicator */
        .typing-indicator {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 14px 20px;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            width: fit-content;
        }

        .typing-dot {
            width: 8px;
            height: 8px;
            background: var(--accent-blue);
            border-radius: 50%;
            animation: typingBounce 1.4s infinite ease-in-out;
        }

        .typing-dot:nth-child(1) { animation-delay: -0.32s; }
        .typing-dot:nth-child(2) { animation-delay: -0.16s; }

        @keyframes typingBounce {
            0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
            40% { transform: scale(1.0); opacity: 1; }
        }

        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 currentColor; opacity: 1; }
            70% { box-shadow: 0 0 0 10px currentColor; opacity: 0; }
            100% { box-shadow: 0 0 0 0 currentColor; opacity: 0; }
        }

        /* Scrollbar Styling */
        .chat-history::-webkit-scrollbar {
            width: 8px;
        }

        .chat-history::-webkit-scrollbar-track {
            background: transparent;
        }

        .chat-history::-webkit-scrollbar-thumb {
            background: rgba(100, 150, 255, 0.2);
            border-radius: 4px;
        }

        .chat-history::-webkit-scrollbar-thumb:hover {
            background: rgba(100, 150, 255, 0.4);
        }
    </style>
</head>
<body>
    <!-- Clean Login Popup Modal (optional, triggered on first send if enabled) -->
    <div id="login-modal" style="display:none; position:fixed; inset:0; background:rgba(10,14,23,0.85); z-index:300; align-items:center; justify-content:center;">
        <div style="background:#111827; border:1px solid rgba(100,150,255,0.25); border-radius:20px; padding:32px 36px; width:100%; max-width:360px; box-shadow:0 20px 60px rgba(0,0,0,0.6);">
            <div style="font-family:'Syne',sans-serif; font-size:20px; font-weight:700; color:white; margin-bottom:6px; text-align:center;">Sign in to continue</div>
            <div style="color:#64748b; font-size:13px; margin-bottom:24px; text-align:center;">This session requires authentication</div>
            
            <input id="login-username" type="text" placeholder="Username" value="user"
                   style="width:100%; background:#0f172a; border:1px solid rgba(100,150,255,0.2); padding:12px 14px; border-radius:10px; color:white; margin-bottom:10px; font-size:14px; outline:none;">
            <input id="login-password" type="password" placeholder="Password"
                   style="width:100%; background:#0f172a; border:1px solid rgba(100,150,255,0.2); padding:12px 14px; border-radius:10px; color:white; margin-bottom:18px; font-size:14px; outline:none;">
            
            <button id="login-submit-btn"
                    style="width:100%; background:linear-gradient(135deg,#6496ff,#8b5cf6); border:none; padding:12px; border-radius:10px; color:white; font-weight:700; font-size:14px; cursor:pointer; transition:all 0.2s;">
                Sign In
            </button>
            
            <div id="login-error" style="color:#f87171; font-size:12px; margin-top:10px; text-align:center; display:none;"></div>
            
            <div style="margin-top:16px; text-align:center;">
                <button id="login-cancel-btn" style="background:none; border:none; color:#64748b; font-size:12px; cursor:pointer;">Cancel</button>
            </div>
        </div>
    </div>

    <div class="sidebar">
        <div class="logo-area">
            <div class="logo-icon">⚡</div>
            <div class="logo-title">DeepSeek</div>
        </div>
        <a href="/console" style="display:block;margin:14px 0;padding:10px 12px;border:1px solid rgba(0,217,255,0.35);color:#d6ff4a;text-decoration:none;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;">Open Orbit agent</a>
        
        <div class="sidebar-section-title">Session Info</div>
        <div class="session-card">
            <div class="session-meta">
                <div class="session-label">Active Date</div>
                <div id="session-date" class="session-value">Initializing...</div>
            </div>
            <div class="session-meta" style="margin-top: 12px;">
                <div class="session-label">Mode</div>
                <div class="session-value">Advanced Python Console</div>
            </div>
        </div>
        
        <!-- Admin link intentionally hidden from user panel for security -->
        <!-- <div class="sidebar-footer"> ... </div> -->
    </div>

    <div class="chat-container">
        <div class="chat-header">
            <div class="chat-header-title">Advanced AI Console</div>
            <div style="display:flex; align-items:center; gap:12px;">
                <button id="header-login-btn" class="header-login-btn" style="display:none; background:rgba(100,150,255,0.1); border:1px solid rgba(100,150,255,0.3); color:#6496ff; padding:6px 14px; border-radius:999px; font-size:12px; font-weight:600; cursor:pointer;">
                    Sign in
                </button>
                <div class="status-badge">
                    <div class="status-dot"></div>
                    Active & Ready
                </div>
            </div>
        </div>

        <div id="chat-history" class="chat-history">
            <div class="message-row bot">
                <div class="message-bubble">
                    <strong>Welcome to Advanced DeepSeek Console</strong><br><br>
                    I provide detailed, production-ready code with:<br>
                    • Complete implementations (simple + optimized)<br>
                    • Time/Space complexity analysis<br>
                    • Practical examples & edge cases<br>
                    • Copy & Download buttons for easy use<br><br>
                    Ask me anything about Python, algorithms, or programming!
                </div>
            </div>
        </div>

        <div class="chat-input-area">
            <div class="input-wrapper">
                <textarea id="chat-input" class="chat-input" placeholder="Ask me to implement a Python algorithm, explain a concept, or write code..."></textarea>
                <button id="send-btn" class="send-btn">
                    <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"></path></svg>
                </button>
            </div>
        </div>
    </div>

    <script>
        const chatInput = document.getElementById("chat-input");
        const sendBtn = document.getElementById("send-btn");
        const chatHistory = document.getElementById("chat-history");
        const sessionDateVal = document.getElementById("session-date");
        const loginModal = document.getElementById("login-modal");
        const headerLoginBtn = document.getElementById("header-login-btn");

        // Defensive checks — prevent silent script abort before listeners are attached
        if (!chatInput) console.warn('[DASHBOARD-UI] chat-input not found');
        if (!sendBtn)   console.warn('[DASHBOARD-UI] send-btn not found');
        if (!chatHistory) console.warn('[DASHBOARD-UI] chat-history not found');
        if (!sessionDateVal) console.warn('[DASHBOARD-UI] session-date not found');
        if (!loginModal) console.warn('[DASHBOARD-UI] login-modal not found');
        if (!headerLoginBtn) console.warn('[DASHBOARD-UI] header-login-btn not found');

        // === Optional User Login (Popup style - non-blocking) ===
        const cfg = window.DASHBOARD_CONFIG || { requireUserAuth: false };
        let pendingPrompt = null; // store message if user needs to login before sending

        function isUserAuthenticated() {
            if (!cfg.requireUserAuth) return true;
            return localStorage.getItem("user_authenticated") === "true";
        }

        function showLoginPopup(afterLoginCallback = null) {
            console.log('[DASHBOARD-UI] showLoginPopup called (afterLoginCallback=' + !!afterLoginCallback + ')');
            if (!loginModal) {
                console.warn('[DASHBOARD-UI] loginModal element not found!');
                return;
            }
            loginModal.style.display = "flex";
            
            const usernameInput = document.getElementById("login-username");
            const passwordInput = document.getElementById("login-password");
            const errorEl = document.getElementById("login-error");
            const submitBtn = document.getElementById("login-submit-btn");
            const cancelBtn = document.getElementById("login-cancel-btn");

            errorEl.style.display = "none";
            passwordInput.value = "";

            const tryLogin = () => {
                const u = usernameInput.value.trim();
                const p = passwordInput.value.trim();

                const passwordConfigured = !!cfg.userPassword;
                const passwordMatches = !passwordConfigured || (p === cfg.userPassword);

                console.log('[DASHBOARD-UI] tryLogin attempt', {u, passwordConfigured, passwordMatches});

                if (u === cfg.userUsername && passwordMatches) {
                    localStorage.setItem("user_authenticated", "true");
                    loginModal.style.display = "none";
                    
                    if (headerLoginBtn) headerLoginBtn.style.display = "none";
                    
                    console.log('[DASHBOARD-UI] Login successful (passwordConfigured=' + passwordConfigured + ')');

                    // If there was a pending message from before the popup, send it now
                    if (pendingPrompt) {
                        const promptToSend = pendingPrompt;
                        pendingPrompt = null;
                        setTimeout(() => {
                            sendMessageDirect(promptToSend);
                        }, 80);
                    } else if (afterLoginCallback) {
                        afterLoginCallback();
                    }
                } else {
                    errorEl.textContent = passwordConfigured 
                        ? "Invalid username or password" 
                        : "Username mismatch (no password configured)";
                    errorEl.style.display = "block";
                    console.warn('[DASHBOARD-UI] Login failed');
                }
            };

            submitBtn.onclick = tryLogin;
            passwordInput.onkeydown = (e) => { if (e.key === "Enter") tryLogin(); };
            cancelBtn.onclick = () => {
                loginModal.style.display = "none";
                pendingPrompt = null;
            };
        }

        // Show small "Sign in" button in header if auth required and not logged in
        if (cfg.requireUserAuth && !isUserAuthenticated()) {
            if (headerLoginBtn) {
                headerLoginBtn.style.display = "inline-block";
                headerLoginBtn.onclick = () => showLoginPopup();
            }
        }

        // Set today's date
        const todayStr = new Date().toLocaleDateString('en-US', { 
            year: 'numeric', 
            month: 'long', 
            day: 'numeric' 
        });
        sessionDateVal.textContent = todayStr;

        // Auto-growing textarea
        chatInput.addEventListener("input", function() {
            this.style.height = "auto";
            this.style.height = Math.min(this.scrollHeight, 140) + "px";
        });

        // Submit on Enter
        chatInput.addEventListener("keydown", function(e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        sendBtn.addEventListener("click", sendMessage);

        let lastUserPrompt = "";  // For Regenerate + quick actions

        // Internal direct send (used after login popup succeeds)
        async function sendMessageDirect(promptText) {
            if (!promptText) return;

            lastUserPrompt = promptText;

            chatInput.value = "";
            chatInput.style.height = "52px";

            appendMessage(promptText, "user");

            const typingIndicator = appendTypingIndicator();
            chatHistory.scrollTop = chatHistory.scrollHeight;

            console.log('[DASHBOARD-UI] sendMessageDirect → preparing fetch to /api/chat');
            try {
                const response = await fetch("/api/chat", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ prompt: promptText })
                });
                
                console.log('[DASHBOARD-UI] fetch /api/chat completed, status=', response.status);
                const data = await response.json();
                typingIndicator.remove();

                if (data.success) {
                    console.log('[DASHBOARD-UI] backend success, response length=', (data.response || '').length);
                    const botMsg = appendMessage(data.response, "bot");
                    if (botMsg) attachQuickActions(botMsg, lastUserPrompt);
                } else {
                    console.warn('[DASHBOARD-UI] backend returned success=false');
                    const botMsg = appendMessage(data.response || "⚠️ Error: Failed to get AI response.", "bot");
                    if (botMsg) attachQuickActions(botMsg, lastUserPrompt);
                }
            } catch (err) {
                console.error('[DASHBOARD-UI] fetch /api/chat failed', err);
                typingIndicator.remove();
                appendMessage("❌ Connection Error: Could not reach the dashboard proxy. Is the dashboard server running?", "bot");
            }
            
            chatHistory.scrollTop = chatHistory.scrollHeight;
        }

        async function sendMessage() {
            const promptText = chatInput.value.trim();
            if (!promptText) return;

            console.log('[DASHBOARD-UI] sendMessage invoked', {
                promptLen: promptText.length,
                requireUserAuth: cfg.requireUserAuth,
                authed: isUserAuthenticated()
            });

            // If optional login is required and not yet signed in, show the popup as a side-effect
            // but do NOT block the actual send (core chat functionality must always work).
            if (cfg.requireUserAuth && !isUserAuthenticated()) {
                console.log('[DASHBOARD-UI] Auth required — showing popup (non-blocking)');
                appendMessage("🔒 Sign-in required for this session (popup opened). Your message will still be sent.", "bot");
                showLoginPopup();
                // Still send the message immediately (popup is just for "claiming" the session)
            }

            // Always send (the whole point of the dashboard)
            await sendMessageDirect(promptText);
        }

        let codeBlockId = 0;

        // Advanced response preprocessor for ChatGPT-style coding answers
        function preprocessForCoding(rawText) {
            // Keep every fenced language as the model sent it. Relabeling or
            // pulling out `import` lines used to turn Swift, JS, and SQL into Python.
            return rawText || "";
        }

        function copyCode(btnEl, id) {
            const codeEl = document.getElementById('codeblock-' + id);
            if (!codeEl) return;

            navigator.clipboard.writeText(codeEl.innerText).then(() => {
                const originalHTML = btnEl.innerHTML;
                btnEl.classList.add('copied');
                btnEl.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg>';
                
                setTimeout(() => {
                    btnEl.classList.remove('copied');
                    btnEl.innerHTML = originalHTML;
                }, 1800);
            });
        }

        function downloadCode(btnEl, id, lang = 'txt') {
            const codeEl = document.getElementById('codeblock-' + id);
            if (!codeEl) return;

            const codeText = codeEl.innerText;
            const element = document.createElement("a");
            element.setAttribute("href", "data:text/plain;charset=utf-8," + encodeURIComponent(codeText));
            
            const langExt = {
                'python': 'py',
                'py': 'py',
                'javascript': 'js',
                'js': 'js',
                'typescript': 'ts',
                'ts': 'ts',
                'json': 'json',
                'bash': 'sh',
                'sh': 'sh',
                'shell': 'sh',
                'html': 'html',
                'css': 'css',
                'sql': 'sql',
                'swift': 'swift',
                'go': 'go',
                'rust': 'rs',
                'rs': 'rs',
                'java': 'java',
                'kotlin': 'kt',
                'kt': 'kt',
                'c': 'c',
                'cpp': 'cpp',
                'c++': 'cpp',
                'csharp': 'cs',
                'cs': 'cs',
                'php': 'php',
                'ruby': 'rb',
                'rb': 'rb'
            }[lang.toLowerCase()] || 'txt';
            
            // Smart filename detection for Python (look for function/class name)
            let filename = `code_${Date.now()}`;
            if (lang.toLowerCase() === 'python') {
                const funcMatch = codeText.match(/^(?:async\\s+)?def\\s+([a-zA-Z_][a-zA-Z0-9_]*)/m);
                const classMatch = codeText.match(/^class\\s+([a-zA-Z_][a-zA-Z0-9_]*)/m);
                if (funcMatch) filename = funcMatch[1];
                else if (classMatch) filename = classMatch[1];
                else filename = 'solution';
            }
            
            element.setAttribute("download", `${filename}.${langExt}`);
            element.style.display = "none";
            document.body.appendChild(element);
            element.click();
            document.body.removeChild(element);
        }

        function appendMessage(text, sender) {
            const row = document.createElement("div");
            row.classList.add("message-row", sender);
            
            const bubble = document.createElement("div");
            bubble.classList.add("message-bubble");

            if (sender === "bot") {
                // === Advanced ChatGPT-style bot message rendering ===
                
                // 1. Add premium response header with "Copy entire response" (very ChatGPT-like)
                const responseHeader = document.createElement('div');
                responseHeader.className = 'response-header';
                responseHeader.innerHTML = `
                    <div class="response-header-left">
                        <span class="ai-label">DeepSeek</span>
                    </div>
                    <button class="response-copy-btn" title="Copy entire response">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                        </svg>
                        <span>Copy response</span>
                    </button>
                `;
                
                const copyAllBtn = responseHeader.querySelector('.response-copy-btn');
                copyAllBtn.addEventListener('click', () => {
                    const originalHTML = copyAllBtn.innerHTML;
                    navigator.clipboard.writeText(text).then(() => {
                        copyAllBtn.innerHTML = `
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                                <polyline points="20 6 9 17 4 12"></polyline>
                            </svg>
                            <span>Copied!</span>
                        `;
                        copyAllBtn.classList.add('copied');
                        setTimeout(() => {
                            copyAllBtn.innerHTML = originalHTML;
                            copyAllBtn.classList.remove('copied');
                        }, 1800);
                    });
                });
                
                bubble.appendChild(responseHeader);

                // 2. Render the actual content
                if (typeof marked === "undefined" || typeof hljs === "undefined") {
                    const fallback = document.createElement('div');
                    fallback.innerHTML = "<pre style='white-space:pre-wrap;font-family:JetBrains Mono,monospace;font-size:13px;line-height:1.6;margin:8px 0 0 0;'>" + 
                        text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + 
                        "</pre>";
                    bubble.appendChild(fallback);
                } else {
                    const renderer = new marked.Renderer();
                    
                    // Advanced code block renderer - ChatGPT style
                    renderer.code = function(codeArg, langArg) {
                        let code, language;
                        if (typeof codeArg === 'object' && codeArg !== null) {
                            code = codeArg.text || codeArg.raw || '';
                            language = codeArg.lang || '';
                        } else {
                            code = codeArg || '';
                            language = langArg || '';
                        }
                        
                        code = code.replace(/\\n$/, '');
                        
                        // Smart language detection for Python
                        let detectedLang = language;
                        if (!detectedLang) {
                            const trimmed = code.trim();
                            if (/^(def |class |async def |from |@|if __name__|print\\()/.test(trimmed)) {
                                detectedLang = 'python';
                            } else if (/^import WidgetKit|^import SwiftUI|^struct \\w+:\\s*TimelineEntry/.test(trimmed)) {
                                detectedLang = 'swift';
                            } else if (/^fn \\w+|^use \\w+::/.test(trimmed)) {
                                detectedLang = 'rust';
                            } else if (/^package \\w+|^func \\w+/.test(trimmed)) {
                                detectedLang = 'go';
                            } else if (/^(SELECT|INSERT|UPDATE|DELETE|WITH)\\b/i.test(trimmed)) {
                                detectedLang = 'sql';
                            } else if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                                detectedLang = 'json';
                            }
                        }
                        
                        const validLang = (detectedLang && hljs.getLanguage(detectedLang)) ? detectedLang : 'plaintext';
                        const highlighted = hljs.highlight(code, { language: validLang }).value;
                        const langLabel = detectedLang || 'Code';
                        const langClass = detectedLang ? detectedLang.toLowerCase() : 'default';
                        const blockId = codeBlockId++;
                        const abbr = langLabel.substring(0, 3).toUpperCase();
                        
                        const downloadLabel = (langClass === 'python') ? 'Download as .py' : 'Download';
                        
                        return '<div class="code-container">' +
                            '<div class="code-header">' +
                                '<div class="code-language">' +
                                    '<div class="code-lang-icon ' + langClass + '">' + abbr + '</div>' +
                                    '<span>' + langLabel + '</span>' +
                                '</div>' +
                                '<div class="code-actions">' +
                                    '<button class="code-btn icon-only" onclick="copyCode(this,' + blockId + ')" title="Copy Code">' +
                                        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
                                    '</button>' +
                                    '<button class="code-btn icon-only" onclick="downloadCode(this,' + blockId + ',\\'' + langClass + '\\')" title="Download">' +
                                        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>' +
                                    '</button>' +
                                '</div>' +
                            '</div>' +
                            '<div class="code-content">' +
                                '<pre><code id="codeblock-' + blockId + '" class="hljs ' + validLang + '">' + highlighted + '</code></pre>' +
                            '</div>' +
                        '</div>';
                    };

                    marked.setOptions({ 
                        renderer: renderer, 
                        breaks: true,
                        gfm: true
                    });
                    
                    // Advanced preprocessing + render (ChatGPT coding assistant format)
                    const processedText = preprocessForCoding(text);
                    const contentDiv = document.createElement('div');
                    contentDiv.innerHTML = marked.parse(processedText);
                    bubble.appendChild(contentDiv);
                }
            } else {
                bubble.textContent = text;
            }
            
            row.appendChild(bubble);
            chatHistory.appendChild(row);
            return row;   // Return for attaching quick actions
        }

        function appendTypingIndicator() {
            const row = document.createElement("div");
            row.classList.add("message-row", "bot");
            
            const indicator = document.createElement("div");
            indicator.classList.add("typing-indicator");
            
            for (let i = 0; i < 3; i++) {
                const dot = document.createElement("div");
                dot.classList.add("typing-dot");
                indicator.appendChild(dot);
            }
            
            row.appendChild(indicator);
            chatHistory.appendChild(row);
            return row;
        }

        // === Quick Action Buttons (Regenerate, Improve, etc.) - Grok/ChatGPT style ===
        function attachQuickActions(messageRow, originalPrompt) {
            if (!originalPrompt) return;

            const actions = document.createElement("div");
            actions.className = "quick-actions";
            
            actions.innerHTML = `
                <button class="quick-btn" data-action="regenerate">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M3 2v6h6"></path><path d="M21 12A9 9 0 0 0 6 5.3L3 8"></path><path d="M21 22v-6h-6"></path><path d="M3 12a9 9 0 0 0 15 6.7l3-2.7"></path></svg>
                    Regenerate
                </button>
                <button class="quick-btn" data-action="better">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 20v-8"></path><path d="M18 20V10"></path><path d="M6 20v-4"></path></svg>
                    Make it better
                </button>
                <button class="quick-btn" data-action="explain">
                    Explain in detail
                </button>
            `;

            actions.querySelectorAll(".quick-btn").forEach(btn => {
                btn.addEventListener("click", async () => {
                    let newPrompt = originalPrompt;
                    
                    if (btn.dataset.action === "regenerate") {
                        newPrompt = originalPrompt;
                    } else if (btn.dataset.action === "better") {
                        newPrompt = `Improve this code/solution with better practices, readability, and performance:\n\n${originalPrompt}`;
                    } else if (btn.dataset.action === "explain") {
                        newPrompt = `Explain the following in great detail with examples:\n\n${originalPrompt}`;
                    }

                    // Send new request
                    appendMessage(newPrompt, "user");
                    const typing = appendTypingIndicator();
                    chatHistory.scrollTop = chatHistory.scrollHeight;

                    try {
                        const response = await fetch("/api/chat", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ prompt: newPrompt })
                        });
                        const data = await response.json();
                        typing.remove();

                        if (data.success) {
                            const newBotRow = appendMessage(data.response, "bot");
                            if (newBotRow) attachQuickActions(newBotRow, newPrompt);
                        } else {
                            appendMessage(data.response || "Error", "bot");
                        }
                    } catch (e) {
                        typing.remove();
                        appendMessage("Connection error during quick action.", "bot");
                    }
                    chatHistory.scrollTop = chatHistory.scrollHeight;
                });
            });

            messageRow.appendChild(actions);
        }
    </script>
</body>
</html>
"""

# Admin dashboard remains the same (keeping previous version)
ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DeepSeek Admin Panel</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=Syne:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0a0e17;
            --card-bg: rgba(20, 24, 33, 0.6);
            --border-glow: rgba(100, 150, 255, 0.15);
            --primary: #6496ff;
            --secondary: #8b5cf6;
            --danger: #ef4444;
            --success: #10b981;
            --text-main: #9ca3af;
            --text-bright: #ffffff;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Space Grotesk', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            min-height: 100vh;
            background-image: radial-gradient(circle at 50% -20%, rgba(100, 150, 255, 0.1) 0%, transparent 60%);
        }

        #auth-gate {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background-color: var(--bg-color);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 100;
        }

        .auth-card {
            background: rgba(20, 24, 33, 0.95);
            border: 1px solid var(--border-glow);
            border-radius: 24px;
            padding: 48px;
            width: 100%;
            max-width: 420px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
            text-align: center;
        }

        .auth-title {
            font-family: 'Syne', sans-serif;
            font-weight: 700;
            font-size: 26px;
            color: var(--text-bright);
            margin-bottom: 28px;
            letter-spacing: -0.5px;
        }

        .input-group {
            margin-bottom: 20px;
            text-align: left;
        }

        .input-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            margin-bottom: 8px;
            color: var(--primary);
            font-weight: 700;
        }

        .form-input {
            width: 100%;
            background: rgba(0, 0, 0, 0.4);
            border: 1px solid rgba(100, 150, 255, 0.2);
            padding: 14px 18px;
            border-radius: 12px;
            color: var(--text-bright);
            font-family: inherit;
            outline: none;
            transition: all 0.3s;
        }

        .form-input:focus {
            border-color: var(--primary);
            box-shadow: 0 0 12px rgba(100, 150, 255, 0.2);
        }

        .auth-btn {
            width: 100%;
            background: linear-gradient(135deg, #6496ff, #8b5cf6);
            border: none;
            padding: 14px;
            border-radius: 12px;
            color: white;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s;
            margin-top: 14px;
            font-size: 14px;
        }

        .auth-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 20px rgba(100, 150, 255, 0.3);
        }

        .dashboard {
            max-width: 1400px;
            margin: 0 auto;
            padding: 48px;
            display: none;
        }

        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 40px;
            border-bottom: 1px solid var(--border-glow);
            padding-bottom: 20px;
        }

        .title-area h1 {
            font-family: 'Syne', sans-serif;
            font-weight: 700;
            color: var(--text-bright);
            font-size: 32px;
            letter-spacing: -0.5px;
        }

        .header-btn {
            background: rgba(239, 68, 68, 0.12);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: var(--danger);
            padding: 10px 18px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s;
            font-weight: 600;
            font-size: 13px;
        }

        .header-btn:hover {
            background: var(--danger);
            color: white;
        }

        .header-btn.secondary {
            background: rgba(100, 150, 255, 0.1);
            border-color: rgba(100, 150, 255, 0.3);
            color: var(--primary);
        }

        .header-btn.secondary:hover {
            background: var(--primary);
            color: white;
        }

        .header-btn.danger {
            background: rgba(239, 68, 68, 0.18);
            border-color: rgba(239, 68, 68, 0.45);
        }

        .subtitle {
            font-size: 12px;
            color: #64748b;
            margin-top: 4px;
            font-weight: 500;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 24px;
            margin-bottom: 40px;
        }

        .metric-card {
            background: var(--card-bg);
            border: 1px solid var(--border-glow);
            border-radius: 20px;
            padding: 28px;
            position: relative;
            overflow: hidden;
        }

        .metric-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: #888;
            margin-bottom: 12px;
            font-weight: 700;
        }

        .metric-value {
            font-family: 'Syne', sans-serif;
            font-size: 28px;
            font-weight: 700;
            color: white;
            word-break: break-all;
            margin-bottom: 8px;
        }

        .layout-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 30px;
            margin-bottom: 40px;
        }

        .panel {
            background: var(--card-bg);
            border: 1px solid var(--border-glow);
            border-radius: 24px;
            padding: 32px;
        }

        .panel-title {
            font-family: 'Syne', sans-serif;
            font-size: 18px;
            font-weight: 700;
            color: white;
            margin-bottom: 28px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--primary);
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-glow);
            font-weight: 700;
        }

        td {
            padding: 16px;
            font-size: 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            color: var(--text-bright);
        }

        .status-tag {
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
        }

        .status-tag.success {
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .status-tag.error {
            background: rgba(239, 68, 68, 0.15);
            color: var(--danger);
            border: 1px solid rgba(239, 68, 68, 0.3);
        }

        .console {
            background: #050608;
            border: 1px solid #1a2233;
            border-radius: 14px;
            padding: 20px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            height: 380px;
            overflow-y: auto;
            color: #10b981;
            white-space: pre-wrap;
            box-shadow: inset 0 2px 10px rgba(0, 0, 0, 0.8);
        }
    </style>
</head>
<body>
    <div id="auth-gate">
        <div class="auth-card">
            <div class="auth-title">Admin Access</div>
            <div class="input-group">
                <div class="input-label">Username</div>
                <input type="text" id="username-input" class="form-input" placeholder="admin">
            </div>
            <div class="input-group">
                <div class="input-label">Password</div>
                <input type="password" id="password-input" class="form-input" placeholder="••••••••">
            </div>
            <button id="login-btn" class="auth-btn">Authenticate</button>
        </div>
    </div>

    <div class="dashboard" id="dashboard-view">
        <div class="header">
            <div class="title-area">
                <h1>DeepSeek • Admin Control Center</h1>
                <div class="subtitle">Real-time • Ephemeral Session • Professional</div>
            </div>
            <div style="display: flex; gap: 10px; align-items: center;">
                <button id="health-btn" class="header-btn secondary">Check Backend Health</button>
                <button id="clear-history-btn" class="header-btn danger">Clear Session History</button>
                <button id="logout-btn" class="header-btn">Logout</button>
            </div>
        </div>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Status</div>
                <div class="metric-value">Active</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Thread Date</div>
                <div id="active-session-date" class="metric-value">--</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Session URL</div>
                <div id="active-session-url" style="font-size: 12px; margin-top: 8px; color: var(--primary);">Checking...</div>
            </div>
        </div>

        <div class="layout-grid">
            <div class="panel">
                <div class="panel-title">Request History</div>
                <table>
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Query</th>
                            <th>Duration</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody id="requests-tbody">
                        <tr><td colspan="4" style="text-align: center; color: #666;">No requests yet</td></tr>
                    </tbody>
                </table>
            </div>

            <div class="panel">
                <div class="panel-title">Session Files</div>
                <div id="files-list" style="display: flex; flex-direction: column; gap: 12px;">
                    <div style="font-size: 13px; color: #666;">No files recorded</div>
                </div>
            </div>
        </div>

        <div class="panel" style="margin-top: 30px;">
            <div class="panel-title">System Logs</div>
            <div id="diagnostics-console" class="console">Initializing...</div>
        </div>
    </div>

    <script>
        const authGate = document.getElementById("auth-gate");
        const dashboardView = document.getElementById("dashboard-view");
        const loginBtn = document.getElementById("login-btn");
        const logoutBtn = document.getElementById("logout-btn");
        const usernameInput = document.getElementById("username-input");
        const passwordInput = document.getElementById("password-input");
        const sessionDateVal = document.getElementById("active-session-date");
        const sessionUrlVal = document.getElementById("active-session-url");
        const requestsTbody = document.getElementById("requests-tbody");
        const filesListVal = document.getElementById("files-list");
        const consoleVal = document.getElementById("diagnostics-console");

        let authKey = localStorage.getItem("admin_auth_key");

        if (authKey) verifyAndEstablish();

        loginBtn.addEventListener("click", async () => {
            const username = usernameInput.value.trim();
            const password = passwordInput.value.trim();
            
            try {
                const res = await fetch("/api/admin/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ username, password })
                });
                
                if (res.ok) {
                    const data = await res.json();
                    authKey = data.token;
                    localStorage.setItem("admin_auth_key", authKey);
                    verifyAndEstablish();
                } else {
                    alert("Invalid credentials. Make sure ADMIN_USERNAME and ADMIN_PASSWORD are correctly set in .env");
                }
            } catch (e) {
                alert("Login failed. Is the dashboard server running?");
            }
        });

        logoutBtn.addEventListener("click", () => {
            localStorage.removeItem("admin_auth_key");
            location.reload();
        });

        // Advanced Admin Actions
        const healthBtn = document.getElementById("health-btn");
        const clearBtn = document.getElementById("clear-history-btn");

        if (healthBtn) {
            healthBtn.addEventListener("click", async () => {
                healthBtn.textContent = "Checking...";
                try {
                    const res = await fetch(`/api/admin/health?auth=${authKey}`);
                    const data = await res.json();
                    const status = data.status === "healthy" ? "✅ Healthy" : "❌ Unreachable";
                    alert(`Backend Status: ${status}\nURL: ${data.backend_url}\n${data.error || ""}`);
                } catch (e) {
                    alert("Health check failed");
                }
                healthBtn.textContent = "Check Backend Health";
            });
        }

        if (clearBtn) {
            clearBtn.addEventListener("click", async () => {
                if (!confirm("Clear all in-memory request history for this session?")) return;
                
                try {
                    const res = await fetch(`/api/admin/clear-history?auth=${authKey}`, { method: "POST" });
                    if (res.ok) {
                        alert("Session history cleared.");
                        requestsTbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #666;">No requests yet</td></tr>';
                    }
                } catch (e) {
                    alert("Failed to clear history");
                }
            });
        }

        async function verifyAndEstablish() {
            authGate.style.display = "none";
            dashboardView.style.display = "block";
            pollStats();
            setInterval(pollStats, 4000); // More real-time
        }

        async function pollStats() {
            try {
                const response = await fetch(`/api/admin/stats?auth=${authKey}`);
                if (response.status === 401) {
                    localStorage.removeItem("admin_auth_key");
                    location.reload();
                    return;
                }
                
                const data = await response.json();
                
                if (data.daily_session) {
                    sessionUrlVal.textContent = data.daily_session.chat_url || "N/A";
                    sessionDateVal.textContent = data.daily_session.date || "N/A";
                }

                if (data.request_history && data.request_history.length > 0) {
                    requestsTbody.innerHTML = "";
                    data.request_history.forEach(req => {
                        const tr = document.createElement("tr");
                        const statusClass = req.status.startsWith("Success") ? "success" : "error";
                        tr.innerHTML = `
                            <td>${req.timestamp}</td>
                            <td style="max-width: 250px; overflow: hidden; text-overflow: ellipsis;">${req.prompt}</td>
                            <td>${req.duration}s</td>
                            <td><span class="status-tag ${statusClass}">${req.status}</span></td>
                        `;
                        requestsTbody.appendChild(tr);
                    });
                }

                if (data.system_logs && data.system_logs.length > 0) {
                    consoleVal.textContent = data.system_logs.join("\n");
                    consoleVal.scrollTop = consoleVal.scrollHeight;
                }
            } catch (err) {
                console.error("Poll error:", err);
            }
        }
    </script>
</body>
</html>
"""

# API Routes
@app.get("/", response_class=HTMLResponse)
async def user_dashboard():
    # Inject auth config into the page (whitespace-tolerant so indentation in the giant string doesn't break it)
    auth_config = f"""
    <script>
        window.DASHBOARD_CONFIG = {{
            requireUserAuth: {str(REQUIRE_USER_AUTH).lower()},
            userUsername: "{USER_USERNAME}",
            userPassword: "{USER_PASSWORD}"
        }};
    </script>
    """
    html = re.sub(
        r'\s*<!--\s*AUTH_CONFIG_INJECTION\s*-->\s*',
        auth_config,
        USER_DASHBOARD_HTML,
        flags=re.IGNORECASE
    )
    return HTMLResponse(content=html)

# Dynamic admin route from .env (ADMIN_PATH)
@app.get(ADMIN_PATH, response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)  # Always keep /admin as fallback
async def admin_dashboard():
    return HTMLResponse(content=ADMIN_DASHBOARD_HTML)

@app.post("/api/agent")
async def api_agent(data: AgentRequest):
    payload = {"prompt": data.prompt, "workspace": data.workspace, "max_steps": data.max_steps}
    return await _proxy_json("POST", "/agent", payload)

async def _proxy_json(method: str, path: str, payload: dict | None):
    backend = os.getenv("BACKEND_URL", "http://127.0.0.1:8000/chat")
    root = backend[:-5] if backend.endswith("/chat") else backend.rstrip("/")
    url = root + path
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    bearer = os.getenv("API_BEARER_TOKEN", "")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    def _call():
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=600) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except Exception:
                detail = raw
            return {"success": False, "response": f"Backend returned HTTP {exc.code}: {detail}", "summary": str(detail)}
        except urllib.error.URLError as exc:
            return {"success": False, "response": f"Cannot connect to backend at {url}. ({exc.reason})", "summary": str(exc.reason)}

    return await asyncio.to_thread(_call)

@app.post("/api/chat")
async def api_chat(data: ChatRequest):
    print(f"[DASHBOARD] Received chat request: {data.prompt[:100]}...")
    start_time = time.time()
    
    # Proxy to backend - using asyncio.to_thread so we don't block the event loop
    # (this matches how app.py handles its long-running work)
    backend_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000/chat")
    payload = json.dumps({"prompt": data.prompt}).encode("utf-8")
    
    headers = {"Content-Type": "application/json"}
    bearer_token = os.getenv("API_BEARER_TOKEN", "413eff4ff6b33d9f7ac0c4e759255251f06ebc56fef1742f51e05bcf77b96ab00dc577d194dcd343b44f429f5bd5f5895a032111f5009aa14cce223a606e8604")
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    def _do_blocking_request():
        req = urllib.request.Request(
            backend_url,
            data=payload,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=350) as response:
            return response.read().decode("utf-8")

    try:
        # Run the blocking urllib call in a thread pool so FastAPI doesn't freeze
        res_data = await asyncio.to_thread(_do_blocking_request)
        parsed_res = json.loads(res_data)
        
        duration = time.time() - start_time
        request_history.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "prompt": data.prompt[:80],
            "response_length": len(parsed_res.get("response", "")),
            "duration": round(duration, 2),
            "status": "Success" if parsed_res.get("success") else "Failed"
        })
        if len(request_history) > 100:
            request_history.pop(0)
            
        print(f"[DASHBOARD] Backend responded successfully in {duration:.1f}s")
        return parsed_res
        
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
            error_detail = json.loads(error_body).get("detail", error_body)
        except Exception:
            error_detail = error_body or str(e)
        
        duration = time.time() - start_time
        request_history.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "prompt": data.prompt[:80],
            "response_length": 0,
            "duration": round(duration, 2),
            "status": f"HTTP {e.code}"
        })
        if len(request_history) > 100:
            request_history.pop(0)
            
        print(f"[DASHBOARD] Backend error {e.code}: {error_detail}")
        return {
            "success": False,
            "response": f"Backend returned HTTP {e.code}: {error_detail}"
        }
        
    except urllib.error.URLError as e:
        duration = time.time() - start_time
        request_history.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "prompt": data.prompt[:80],
            "response_length": 0,
            "duration": round(duration, 2),
            "status": "Connection Failed"
        })
        if len(request_history) > 100:
            request_history.pop(0)
            
        print(f"[DASHBOARD] Connection error to backend: {e.reason}")
        return {
            "success": False,
            "response": f"Cannot connect to backend at {backend_url}. Is the main automation server running? ({str(e.reason)})"
        }
        
    except Exception as e:
        duration = time.time() - start_time
        request_history.append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "prompt": data.prompt[:80],
            "response_length": 0,
            "duration": round(duration, 2),
            "status": "Error"
        })
        if len(request_history) > 100:
            request_history.pop(0)
            
        print(f"[DASHBOARD] Proxy error: {e}")
        return {
            "success": False,
            "response": f"Proxy Error: {str(e)}"
        }

# ============================================
# ADMIN AUTHENTICATION (uses .env)
# ============================================

@app.post("/api/admin/login")
async def admin_login(data: dict):
    username = data.get("username", "")
    password = data.get("password", "")
    
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD and ADMIN_PASSWORD:
        # Return a simple session token (the password itself acts as proof for this internal tool)
        return {"success": True, "token": ADMIN_PASSWORD}
    
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/api/admin/stats")
async def get_admin_stats(auth: str = None):
    if not ADMIN_PASSWORD or auth != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    daily_session = {}
    session_file = os.path.join(PROJECT_ROOT, "daily_session.json")
    if os.path.exists(session_file):
        try:
            with open(session_file, "r") as f:
                daily_session = json.load(f)
        except:
            pass

    completion_logs = []
    logs_dir = os.path.join(PROJECT_ROOT, "completion_logs")
    if os.path.exists(logs_dir):
        try:
            files = sorted(
                [f for f in os.listdir(logs_dir) if f.endswith(".txt")],
                key=lambda x: os.path.getmtime(os.path.join(logs_dir, x)),
                reverse=True
            )
            for f in files[:8]:
                fpath = os.path.join(logs_dir, f)
                completion_logs.append({
                    "filename": f,
                    "size": os.path.getsize(fpath),
                    "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(fpath)))
                })
        except:
            pass

    system_logs = []
    log_file = os.path.join(PROJECT_ROOT, "automation.log")
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", errors="ignore") as f:
                lines = f.readlines()
                system_logs = [line.strip() for line in lines[-100:]]
        except:
            pass

    return {
        "daily_session": daily_session,
        "completion_logs_list": completion_logs,
        "system_logs": system_logs,
        "request_history": request_history
    }

# Clear ephemeral history (in-memory only)
@app.post("/api/admin/clear-history")
async def clear_history(auth: str = None):
    if not ADMIN_PASSWORD or auth != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    global request_history
    request_history = []
    return {"success": True, "message": "Session history cleared"}

# Backend health check
@app.get("/api/admin/health")
async def backend_health(auth: str = None):
    if not ADMIN_PASSWORD or auth != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    backend_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000/chat")
    health_url = backend_url.rsplit("/", 1)[0] + "/docs"  # FastAPI docs as health indicator
    
    try:
        req = urllib.request.Request(health_url, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {
                "status": "healthy",
                "backend_url": backend_url,
                "response_code": resp.status
            }
    except Exception as e:
        return {
            "status": "unreachable",
            "backend_url": backend_url,
            "error": str(e)
        }