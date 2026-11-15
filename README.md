# HMW 工作台

本仓库依据 `docs/workbench-requirements-design-v0.1.md` 和 `docs/workbench-implementation-plan-v0.1.md` 开发。当前只有本地开发用的个人状态初始切片：本地账号登录、手动保存草稿和发布、历史记录与基本权限检查。

**尚不可试点或用于真实数据。** 尚未实现正式身份认证、项目权限、审计、投入统计、同事查看、知识管理、问答、问题、Worker、任务和审批。SQLite 开发数据不等于计划要求的 PostgreSQL 生产方案，并发和故障恢复尚未验证。

## 本地体验

需要 Python 3.12+、uv、Node.js 和 pnpm。仅在自己的电脑上使用脱敏样例，开发账号和会话只为本地调试准备，不能暴露到网络。

```sh
uv sync --project apps/api
pnpm --dir apps/web install
```

在第一个终端启动 API，替换下面的临时密码：

```sh
cd apps/api
export HMW_DEV_USERNAME=owner
export HMW_DEV_PASSWORD='替换为本地临时密码'
.venv/bin/uvicorn workbench.dev:app --host 127.0.0.1 --port 8000
```

在第二个终端启动界面：

```sh
pnpm --dir apps/web dev
```

访问 Vite 输出的本地地址，用刚才配置的账号和密码登录。重启 API 后需重新登录；个人状态存储在 `apps/api/workbench-dev.db`。默认 `workbench.app:app` 不启用开发登录，也不会信任测试身份请求头。

## 测试

```sh
uv run --project apps/api pytest
pnpm --dir apps/web test
pnpm --dir apps/web build
```

API 测试使用 SQLite 和显式测试身份，不代替 PostgreSQL、真实身份、浏览器端到端或安全测试。
