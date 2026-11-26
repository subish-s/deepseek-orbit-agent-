#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const EXAMPLE_FILE = path.join(ROOT, '.env.example');
const LOCAL_FILE = path.join(ROOT, '.env.local');

console.log('\n🚀 DeepSeek Dashboard Setup\n');

if (fs.existsSync(LOCAL_FILE)) {
  console.log('✅ .env.local already exists.');
  console.log('   You can edit it anytime:');
  console.log('   → ' + LOCAL_FILE);
  console.log('\nTip: Run "npm start" to launch the dashboard.\n');
  process.exit(0);
}

if (!fs.existsSync(EXAMPLE_FILE)) {
  console.error('❌ Error: .env.example not found.');
  console.error('   Cannot create .env.local');
  process.exit(1);
}

try {
  fs.copyFileSync(EXAMPLE_FILE, LOCAL_FILE);
  console.log('✅ Created .env.local from .env.example');
  console.log('\n📝 Next steps:');
  console.log('   1. Open .env.local and update these values:');
  console.log('      - BACKEND_URL (usually http://127.0.0.1:8000/chat)');
  console.log('      - API_BEARER_TOKEN (must match your main backend)');
  console.log('   2. (Optional) Set REQUIRE_USER_AUTH and credentials if needed');
  console.log('\n   Then run: npm start\n');
} catch (err) {
  console.error('❌ Failed to create .env.local');
  console.error(err.message);
  process.exit(1);
}
