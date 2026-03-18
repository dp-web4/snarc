#!/bin/bash
# engram install script
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Installing engram — salience-gated memory for Claude Code"
echo ""

NODE_VERSION=$(node -v 2>/dev/null | sed 's/v//' | cut -d. -f1)
if [ -z "$NODE_VERSION" ] || [ "$NODE_VERSION" -lt 18 ]; then
    echo "Error: Node.js >= 18 required (found: $(node -v 2>/dev/null || echo 'none'))"
    exit 1
fi

echo "Installing dependencies..."
npm install 2>&1 | tail -3

echo "Building..."
npx tsc 2>&1 | tail -3

if [ -f "dist/src/server.js" ] && [ -f "dist/hooks/handlers/post-tool-use.js" ]; then
    echo ""
    echo "engram installed successfully."
    echo ""
    echo "What happens next:"
    echo "  - SessionStart: injects memory briefing into Claude's context"
    echo "  - Every tool use: scored on 5 salience dimensions (SNARC)"
    echo "  - Every prompt: searches for related memories"
    echo "  - After compaction: mid-session dream cycle + re-inject"
    echo "  - Session end: consolidation (heuristic + optional deep dream)"
    echo ""
    echo "CLI: engram stats | search | patterns | dream [--deep] | review | config"
    echo "Data: ~/.engram/projects/<hash>/engram.db (per launch directory)"
else
    echo "Error: Build failed — dist/ files not found"
    exit 1
fi
