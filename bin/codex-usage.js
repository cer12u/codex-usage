#!/usr/bin/env node
// Thin Node wrapper to run the Python CLI.
// This allows: npx codex-usage (when published) or npx . in this repo.

const { spawn } = require('child_process');
const path = require('path');

function findPython() {
  const candidates = [process.env.PYTHON || '', 'python3', 'python'];
  return candidates.find(Boolean);
}

const python = findPython();
if (!python) {
  console.error('Python interpreter not found. Please install python3 or set $PYTHON.');
  process.exit(1);
}

const script = path.join(__dirname, '..', 'codex_token_usage.py');
const args = process.argv.slice(2);

const child = spawn(python, [script, ...args], { stdio: 'inherit' });
child.on('exit', (code) => process.exit(code));

