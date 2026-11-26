# DeepSeek Dashboard - Node.js Version

This is a standalone Node.js replacement for the Python `dashboard_app`.

## Why this exists
The original Python/FastAPI dashboard had persistent issues with the send button not reaching the backend (only GET / and favicon logs appearing).

This Node.js version focuses on reliability and visibility.

## Quick Start

The easiest way:

```bash
cd dashboard-node
npm install
npm run setup          # Creates .env.local for you
# Edit .env.local with your BACKEND_URL and API_BEARER_TOKEN
npm start
```

Alternatively (manual):

```bash
cp .env.example .env.local
# Edit .env.local ...
npm start
```

Then open http://localhost:8001

## Environment Variables

We recommend putting **secrets** (especially the Bearer token) in `.env.local` (this file is gitignored).

### Minimal `.env.local` example:

```env
BACKEND_URL=http://127.0.0.1:8000/chat
API_BEARER_TOKEN=413eff4ff6b33d9f7ac0c4e759255251f06ebc56fef1742f51e05bcf77b96ab00dc577d194dcd343b44f429f5bd5f5895a032111f5009aa14cce223a606e8604

# Optional
REQUIRE_USER_AUTH=false
USER_USERNAME=user
USER_PASSWORD=
```

You can also use `.env` for non-sensitive shared settings. `.env.local` takes precedence.

## Environment Variables Reference

```env
BACKEND_URL=http://127.0.0.1:8000/chat          # URL of your main Python backend
API_BEARER_TOKEN=your_long_token_here          # Must match the token in your main project's .env

REQUIRE_USER_AUTH=false
USER_USERNAME=user
USER_PASSWORD=

ADMIN_USERNAME=admin
ADMIN_PASSWORD=
ADMIN_PATH=/admin
```

## Key Features
- Very visible logging (`[DASHBOARD] ...` in terminal)
- Same proxy contract as the Python version
- Beautiful modern chat UI with:
  - Advanced code blocks (Copy + Download as .py)
  - Quick actions (Regenerate, Make it better, Explain)
  - Optional login popup
- Non-blocking auth (send always works)

## Important
This only replaces the **dashboard layer**. Your main automation server (`app.py`) must still be running on the `BACKEND_URL`.
