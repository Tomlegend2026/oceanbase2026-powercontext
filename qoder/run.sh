#!/usr/bin/env sh
# RecallCoach 一键启动（Linux/macOS）。没有 PowerContext 也能起来，会自动退化为"只按到期排序"。
set -eu

cd "$(dirname "$0")/app"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install -q --upgrade pip
python -m pip install -q -e .

PORT="${PORT:-8080}"
echo "RecallCoach 启动中： http://127.0.0.1:${PORT}"
echo "  - 健康检查： /api/health"
echo "  - 没有 PowerContext 时页面会显示「记忆离线」，其余功能照常"
exec python -m uvicorn recallcoach.main:app --host 127.0.0.1 --port "${PORT}"
