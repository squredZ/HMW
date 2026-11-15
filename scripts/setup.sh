#!/usr/bin/env bash
# Install the checked-in backend and frontend dependency sets.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

missing=0
for tool in uv node npm pnpm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf '缺少必要工具：%s\n' "$tool" >&2
    missing=1
  fi
done
if (( missing )); then
  printf '请先安装上述工具，然后重新运行此脚本。\n' >&2
  exit 1
fi

printf '正在安装后端依赖（uv.lock）...\n'
uv sync --locked --project "$project_root/apps/api"
printf '正在安装只读 Agent 依赖（package-lock.json）...\n'
npm ci --prefix "$project_root/apps/agent" --ignore-scripts
printf '正在安装前端依赖（pnpm-lock.yaml）...\n'
pnpm --dir "$project_root/apps/web" install --frozen-lockfile
printf '\n环境准备完成。请参考 README.md 的“本地体验”启动 API、界面和 Worker。\n'
