require('dotenv').config();                    // Load .env
require('dotenv').config({ path: '.env.local', override: true }); // Load .env.local (takes precedence, good for secrets)

const express = require('express');
const path = require('path');
const https = require('https');
const http = require('http');

const app = express();
const PORT = process.env.PORT || 8001;

// ==================== CONFIG ====================
const BACKEND_URL = process.env.BACKEND_URL || 'http://127.0.0.1:8000/chat';
const API_BEARER_TOKEN = process.env.API_BEARER_TOKEN;   // NO hardcoded fallback

// Validate required token at startup
if (!API_BEARER_TOKEN) {
  console.error('\n❌ ERROR: API_BEARER_TOKEN is not set!');
  console.error('   Please create a .env.local file (recommended) or .env file with:');
  console.error('   API_BEARER_TOKEN=your_token_here\n');
  process.exit(1);
}

const REQUIRE_USER_AUTH = ['true', '1', 'yes'].includes((process.env.REQUIRE_USER_AUTH || 'false').toLowerCase());
const USER_USERNAME = process.env.USER_USERNAME || 'user';
const USER_PASSWORD = process.env.USER_PASSWORD || '';

const ADMIN_USERNAME = process.env.ADMIN_USERNAME || 'admin';
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || '';
const ADMIN_PATH = (process.env.ADMIN_PATH || '/admin').replace(/\/$/, '');

// In-memory request history (same as Python version)
let requestHistory = [];

// Simple in-memory admin log buffer (last ~80 entries)
let adminLogs = [];
function adminLog(msg) {
  const entry = `[${new Date().toISOString().slice(11,19)}] ${msg}`;
  adminLogs.unshift(entry);
  if (adminLogs.length > 80) adminLogs.pop();
  // Also print to console for operator visibility
  console.log('[ADMIN]', msg);
}

// ==================== MIDDLEWARE ====================
app.use(express.json({ limit: '1mb' }));
app.use(express.static(path.join(__dirname, 'public')));

// ==================== LOGGING (user wanted to see everything) ====================
function log(...args) {
  const msg = args.join(' ');
  console.log('[DASHBOARD]', ...args);
  // Feed important events to admin panel
  if (msg.includes('chat request') || msg.includes('Backend responded') || msg.includes('Proxy error') || msg.includes('401')) {
    adminLog(msg);
  }
}

app.get('/console', (req, res) => {
  res.sendFile(path.join(__dirname, '..', 'console', 'index.html'));
});

app.get('/api/meta', (req, res) => {
  proxyBackend('/meta', 'GET', null, res);
});

app.post('/api/agent', (req, res) => {
  const body = {
    prompt: (req.body && req.body.prompt) || '',
    workspace: req.body ? req.body.workspace : undefined,
    max_steps: req.body ? req.body.max_steps : 8,
  };
  log(`Received agent request: ${body.prompt.substring(0, 100)}...`);
  proxyBackend('/agent', 'POST', body, res, body.prompt);
});

function proxyBackend(apiPath, method, payload, res, historyPrompt) {
  const headers = { 'Content-Type': 'application/json' };
  if (API_BEARER_TOKEN) headers.Authorization = `Bearer ${API_BEARER_TOKEN}`;
  const base = new URL(BACKEND_URL);
  const origin = `${base.protocol}//${base.host}`;
  const target = new URL(apiPath, origin);
  const client = target.protocol === 'https:' ? https : http;
  const body = payload == null ? null : JSON.stringify(payload);
  const started = Date.now();
  const proxyReq = client.request({
    hostname: target.hostname,
    port: target.port || (target.protocol === 'https:' ? 443 : 80),
    path: target.pathname + target.search,
    method,
    headers,
    timeout: 600000,
  }, (proxyRes) => {
    let data = '';
    proxyRes.on('data', (chunk) => { data += chunk; });
    proxyRes.on('end', () => {
      let parsed;
      try { parsed = JSON.parse(data); }
      catch (e) { parsed = { success: false, response: data || 'Invalid JSON from backend' }; }
      if (historyPrompt) {
        requestHistory.unshift({
          timestamp: new Date().toISOString(),
          prompt: historyPrompt.substring(0, 80),
          duration: (Date.now() - started) / 1000,
          status: parsed.success ? 'Success' : 'Failed',
        });
        if (requestHistory.length > 100) requestHistory.pop();
      }
      res.status(proxyRes.statusCode || 200).json(parsed);
    });
  });
  proxyReq.on('error', (err) => {
    res.status(502).json({
      success: false,
      response: `Cannot connect to backend at ${origin}. Is the main API running?`,
      summary: err.message,
    });
  });
  if (body) proxyReq.write(body);
  proxyReq.end();
}

// ==================== PROXY /api/chat (the most important part) ====================
app.post('/api/chat', async (req, res) => {
  const prompt = req.body.prompt || '';
  log(`Received chat request: ${prompt.substring(0, 100)}...`);

  const startTime = Date.now();

  // Build headers exactly like the Python version
  const headers = {
    'Content-Type': 'application/json',
  };

  if (API_BEARER_TOKEN) {
    headers['Authorization'] = `Bearer ${API_BEARER_TOKEN}`;
  }

  const payload = JSON.stringify({ prompt });

  const isHttps = BACKEND_URL.startsWith('https://');
  const client = isHttps ? https : http;

  const url = new URL(BACKEND_URL);

  const options = {
    hostname: url.hostname,
    port: url.port || (isHttps ? 443 : 80),
    path: url.pathname + url.search,
    method: 'POST',
    headers,
    timeout: 350000,
  };

  const proxyReq = client.request(options, (proxyRes) => {
    let data = '';

    proxyRes.on('data', chunk => data += chunk);
    proxyRes.on('end', () => {
      const duration = ((Date.now() - startTime) / 1000).toFixed(1);

      let parsed;
      try {
        parsed = JSON.parse(data);
      } catch (e) {
        parsed = { success: false, response: data || 'Invalid JSON from backend' };
      }

      // Record history (same as Python)
      requestHistory.unshift({
        timestamp: new Date().toISOString(),
        prompt: prompt.substring(0, 80),
        duration: parseFloat(duration),
        status: parsed.success ? 'Success' : 'Failed'
      });
      if (requestHistory.length > 100) requestHistory.pop();

      if (proxyRes.statusCode === 401) {
        log(`Backend returned 401 Unauthorized.`);
        console.log('⚠️  401 from backend → The API_BEARER_TOKEN in dashboard-node/.env does not match the one expected by app.py');
        parsed = {
          success: false,
          response: "Authentication failed (401). The Bearer token sent to the backend is invalid or missing. Check that API_BEARER_TOKEN in dashboard-node/.env matches your main .env file."
        };
      } else {
        log(`Backend responded in ${duration}s - success=${parsed.success}`);
      }

      res.json(parsed);
    });
  });

  proxyReq.on('error', (err) => {
    log('Proxy error:', err.message);
    const duration = ((Date.now() - startTime) / 1000).toFixed(1);

    requestHistory.unshift({
      timestamp: new Date().toISOString(),
      prompt: prompt.substring(0, 80),
      duration: parseFloat(duration),
      status: 'Connection Failed'
    });

    res.status(502).json({
      success: false,
      response: `Cannot connect to backend at ${BACKEND_URL}. Is the main automation server running?`
    });
  });

  // Also log when we are about to send the request with/without token
  if (!API_BEARER_TOKEN || API_BEARER_TOKEN.length < 20) {
    log('WARNING: Sending request to backend WITHOUT a valid Bearer token!');
  }

  proxyReq.write(payload);
  proxyReq.end();
});

// ==================== OPTIONAL USER AUTH (same behavior as Python version) ====================
app.post('/api/user/login', (req, res) => {
  const { username, password } = req.body || {};

  const passwordConfigured = !!USER_PASSWORD;
  const passwordMatches = !passwordConfigured || password === USER_PASSWORD;

  if (username === USER_USERNAME && passwordMatches) {
    log('User login successful');
    return res.json({ success: true, token: 'ok' });
  }

  log('User login failed');
  res.status(401).json({ success: false, message: 'Invalid credentials' });
});

// ==================== ADMIN (basic but functional) ====================
app.get(ADMIN_PATH, (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'admin.html'));
});

app.post('/api/admin/login', (req, res) => {
  const { username, password } = req.body || {};
  if (username === ADMIN_USERNAME && password === ADMIN_PASSWORD && ADMIN_PASSWORD) {
    adminLog(`Admin login successful: ${username}`);
    return res.json({ success: true, token: ADMIN_PASSWORD });
  }
  adminLog(`Admin login FAILED for user: ${username || '(empty)'}`);
  res.status(401).json({ success: false });
});

app.get('/api/admin/stats', (req, res) => {
  const auth = req.query.auth;
  if (!ADMIN_PASSWORD || auth !== ADMIN_PASSWORD) {
    return res.status(401).json({ detail: 'Unauthorized' });
  }

  const mem = process.memoryUsage();
  const uptime = process.uptime();

  res.json({
    daily_session: {
      date: new Date().toISOString().split('T')[0],
      chat_url: BACKEND_URL,
      node_version: process.version,
      uptime_seconds: Math.floor(uptime),
      uptime_human: `${Math.floor(uptime / 3600)}h ${Math.floor((uptime % 3600) / 60)}m`,
    },
    request_history: requestHistory,
    system_logs: adminLogs.length > 0 ? adminLogs : ['No admin-visible events yet. Chat requests and backend responses will appear here.'],
    server_info: {
      port: PORT,
      backend: BACKEND_URL,
      memory_rss_mb: Math.round(mem.rss / 1024 / 1024),
      memory_heap_mb: Math.round(mem.heapUsed / 1024 / 1024),
    }
  });
});

app.post('/api/admin/clear-history', (req, res) => {
  const auth = req.query.auth;
  if (!ADMIN_PASSWORD || auth !== ADMIN_PASSWORD) {
    return res.status(401).json({ detail: 'Unauthorized' });
  }
  requestHistory = [];
  adminLog('Request history cleared by admin');
  res.json({ success: true });
});

// ==================== ADMIN HEALTH CHECK (matches Python dashboard behavior) ====================
app.get('/api/admin/health', async (req, res) => {
  const auth = req.query.auth;
  if (!ADMIN_PASSWORD || auth !== ADMIN_PASSWORD) {
    return res.status(401).json({ detail: 'Unauthorized' });
  }

  const start = Date.now();
  try {
    const healthUrl = BACKEND_URL.replace(/\/chat$/, '') + '/health';
    const isHttps = healthUrl.startsWith('https://');
    const client = isHttps ? https : http;

    const url = new URL(healthUrl);
    const options = {
      hostname: url.hostname,
      port: url.port || (isHttps ? 443 : 80),
      path: url.pathname + url.search,
      method: 'GET',
      timeout: 6000,
    };

    const healthReq = client.request(options, (healthRes) => {
      const duration = Date.now() - start;
      adminLog(`Backend health check: ${healthRes.statusCode} (${duration}ms)`);
      res.json({
        status: healthRes.statusCode >= 200 && healthRes.statusCode < 500 ? 'healthy' : 'degraded',
        backend_url: BACKEND_URL,
        response_code: healthRes.statusCode,
        latency_ms: duration,
      });
    });

    healthReq.on('error', (err) => {
      adminLog(`Backend health check failed: ${err.message}`);
      res.json({
        status: 'unreachable',
        backend_url: BACKEND_URL,
        error: err.message,
        latency_ms: Date.now() - start,
      });
    });

    healthReq.on('timeout', () => {
      healthReq.destroy();
      res.json({
        status: 'timeout',
        backend_url: BACKEND_URL,
        error: 'Health check timed out after 6s',
      });
    });

    healthReq.end();
  } catch (e) {
    res.json({
      status: 'error',
      backend_url: BACKEND_URL,
      error: e.message,
    });
  }
});

// ==================== SERVE THE CHAT UI ====================
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', backend: BACKEND_URL });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`
╔════════════════════════════════════════════════════════════╗
║   DeepSeek Dashboard (Node.js)                             ║
╠════════════════════════════════════════════════════════════╣
║   Running on: http://0.0.0.0:${PORT}                        ║
║   Proxying to: ${BACKEND_URL}                               ║
║   Admin Panel : http://0.0.0.0:${PORT}${ADMIN_PATH}          ║
║   Bearer Token: Loaded from environment (.env / .env.local) ║
║   User Auth Required: ${REQUIRE_USER_AUTH}                  ║
╚════════════════════════════════════════════════════════════╝
  `);

  console.log('[DASHBOARD] User auth required for chat:', REQUIRE_USER_AUTH);
  console.log('[DASHBOARD] Bearer token is configured (not hardcoded in source).');
  if (ADMIN_PASSWORD) {
    console.log(`[DASHBOARD] Admin login: username="${ADMIN_USERNAME}" (password set in env)`);
    adminLog('Admin panel ready — login with configured credentials');
  } else {
    console.log('⚠️  [DASHBOARD] ADMIN_PASSWORD is not set — admin panel is disabled for security.');
  }
});