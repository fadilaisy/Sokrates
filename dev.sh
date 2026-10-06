#!/usr/bin/env bash
# dev.sh — Run SkillForge backend and frontend concurrently

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Ensure Node & tools are in PATH if installed in local directories
export PATH="/Users/macbookpro/.opencode/bin:/Users/macbookpro/.local/bin:$PATH"

echo "=========================================="
echo "  SkillForge — Industrial Agentic AI"
echo "=========================================="

# 1. Environment check
if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    echo "⚠️  .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "💡 Please update .env with your GEMINI_API_KEY if needed."
  fi
fi

# 2. Trap for clean shutdown
cleanup() {
  echo ""
  echo "🛑 Stopping SkillForge services..."
  kill $(jobs -p) 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# 3. Start Backend
echo "🚀 Starting FastAPI Backend on http://localhost:8000..."
python3 -m uvicorn backend.main:app --reload --port 8000 &
BACKEND_PID=$!

# 4. Wait 2 seconds for backend to initialize
sleep 2

# 5. Start Frontend
echo "💻 Starting React Frontend on http://localhost:5173..."
cd frontend
npm run dev &
FRONTEND_PID=$!

wait
