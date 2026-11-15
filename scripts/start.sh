#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python="$project_root/apps/api/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  printf '未找到后端虚拟环境，请先运行 ./scripts/setup.sh。\n' >&2
  exit 1
fi
exec "$python" "$project_root/scripts/start.py" "${1:-$project_root/.hmw.toml}"
