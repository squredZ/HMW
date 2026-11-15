# HMW 工作台

本仓库依据 `docs/workbench-requirements-design-v0.1.md` 和 `docs/workbench-implementation-plan-v0.1.md` 开发。当前包含本地开发用的个人状态初始切片和 Markdown/TXT 知识导入雏形：本地账号登录、项目成员权限、手动保存、发布和撤回个人状态、按项目共享和每周投入记录。

**尚不可试点或用于真实数据。** 尚未实现正式身份认证、正式项目身份与授权管理、审计查询与保留策略、投入统计图表、完整知识管理、问答、问题、通用后台任务和审批。SQLite 开发数据不等于计划要求的 PostgreSQL 生产方案，并发和故障恢复尚未验证。

MVP 计划使用 Pi 作为 Agent 运行时，本地知识检索先不接入 EverOS；未来长期记忆通过适配边界扩展。当前 Pi 也尚未完成产品集成。

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
.venv/bin/uvicorn workbench.dev:app --host 127.0.0.1 --port 8000 --no-access-log
```

要演示两个本地账号，可以在启动 API 前使用 `HMW_DEV_USERS_JSON` 配置账号映射，并取消设置单账号变量。例如：

```sh
unset HMW_DEV_USERNAME HMW_DEV_PASSWORD
export HMW_DEV_USERS_JSON='{"owner":"本地密码一","colleague":"本地密码二"}'
```

使用不同浏览器或无痕窗口分别登录；创建项目后可将同事账号加入，再由本人发布可见状态。生产环境不可使用此开发身份方式。

在第二个终端启动界面：

```sh
pnpm --dir apps/web dev
```

在第三个终端启动文本导入 Worker（必须和 API 使用同一数据库与文件存储位置）：

```sh
cd apps/api
.venv/bin/python -m workbench.jobs.worker
```

可在页面选择项目并上传单个 UTF-8 Markdown/TXT，待 Worker 处理后刷新任务、预览并发布。文件默认保存在 `apps/api/workbench-objects/`，此目录已被 Git 忽略；后台通过数据库租约领取任务。当前不支持其他编码的直接导入、草稿编辑、知识列表、重试界面或服务重启后的跨标签页任务查找。

访问 Vite 输出的本地地址，用刚才配置的账号和密码登录。重启 API 后需重新登录；个人状态存储在 `apps/api/workbench-dev.db`。默认 `workbench.app:app` 不启用开发登录，也不会信任测试身份请求头。

## 测试

```sh
uv run --project apps/api pytest
pnpm --dir apps/web test
pnpm --dir apps/web build
```

API 测试使用 SQLite 和显式测试身份，不代替 PostgreSQL、并发控制、真实身份、浏览器端到端或安全测试。本地账号仅供开发，成员管理不等于正式用户管理。

API 请求诊断以 JSON 记录路由模板、响应状态、耗时及请求 ID，不记录正文、查询参数或会话凭据；本地启动关闭 Uvicorn 原始访问日志以免记录查询参数。个人状态并发测试当前使用 SQLite，尚未覆盖 PostgreSQL 或多进程部署。

当前项目和个人状态的成功写操作会在同一数据库事务中记录审计元数据并关联请求 ID；拒绝操作暂通过请求日志记录。尚无审计查询界面、数据库级防篡改或保留清理机制。

导入功能仅供本地脱敏开发验证：尚未验证 PostgreSQL 多进程竞争、Worker 心跳、断电后孤儿文件清理、完整文件上传资源隔离或来源撤销；非 UTF-8 文本需转换后重新上传。
