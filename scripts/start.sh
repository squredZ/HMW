#!/bin/sh
set -eu
project_root="$(CDPATH= cd -P "$(dirname "$0")/.." && pwd -P)"
python="$project_root/apps/api/.venv/bin/python"
if [ ! -x "$python" ]; then
  printf '未找到后端虚拟环境，请先运行 ./scripts/setup.sh。\n' >&2
  exit 1
fi
exec "$python" "$project_root/scripts/start.py" "${1:-$project_root/.hmw.toml}"
