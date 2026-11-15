# 个人 / 团队工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个按项目管理知识、支持授权问答 / 问题 / 后台任务、且个人状态只能由本人手动维护的可试点工作台。

**Architecture:** 模块化单体 API 与独立持久 Worker。PostgreSQL 保存权限、版本、执行状态与审计；对象存储保存来源和产物。模型、文件转换、存储和身份系统通过适配器接入，先提供本地模拟模式。

**Tech Stack:** 建议 React / TypeScript、Python / FastAPI、PostgreSQL、JSON Schema、pytest、Playwright。依赖版本在开工时核对官方文档并锁定，不采用未核验的“最新”版本。

**Spec:** [workbench-requirements-design-v0.1.md](workbench-requirements-design-v0.1.md)。该设计与本计划必须共同交给执行者。未来仓库分别归档到 docs/superpowers/specs/2026-11-15-workbench-design.md 和 docs/superpowers/plans/2026-11-15-workbench-plan.md。

**交付状态：** 本文记录目标和验收步骤，复选框仅在对应步骤的全部验收条件完成后勾选；部分实现不等于任务完成。当前代码和运行方式见 [README](../README.md)，原型尚不可试点或用于真实数据。

### 当前本地开发进展

- 已有负责人本地开发登录、独立临时访客会话，以及个人状态手动保存、发布和访客可见范围控制；正式身份与生产会话管理未完成。
- 已有项目知识文件导入、独立 Worker、草稿预览及发布；负责人可按项目、状态和文件名管理知识，查看正文及原件，调整访客可见性和停用。停用保留正文、原件和审计元数据，但再次发布需重新导入。
- 访客可在项目内检索明确开放的已发布知识，查看提取出的原文及来源；结果不经过模型生成，不保存问答历史，检索有资源上限。完整问答、完整问题单和后台任务仍待实现。
- 已有问题登记基础流程：临时访客对开放知识的项目提交标题与描述，负责人可分页查看并手动标记已解决；幂等、限流、附件、访客进度查询和完整状态流转尚未实现。
- Office 隔离转换、草稿编辑、分类、关系、版本对比、基线、审计查询、保留清理和生产 PostgreSQL 多进程验证等仍待完成；不能据此把任务 2–5 标记为全部完成。
- Pi 集成仍待开发；MVP 不接入 EverOS。

## 实施调整（2026-11-15）

- 新需求：仅负责人拥有长期身份，同事通过独立临时会话访问；原多人账号及项目成员管理步骤被此方向取代。同事通过链接直接访问，内容须由负责人明确选择向访客公开，迁移方案见 [单负责人、临时访客调整草案](single-owner-temporary-sessions.md)。

- 保留 Pi 作为计划中的 Agent 运行时，先使用模拟模型和受控工具；现有验证探针不等于生产集成。
- MVP 不接入 EverOS：不安装运行时依赖，不部署其服务，不使现有功能依赖其可用性。
- 任务 5 优先实现本地、按授权过滤的已发布知识检索。查询与记忆实现之间保留适配边界；具体协议随实际消费者和未来接入需求确定，避免为尚未使用的功能增加部署负担。
- 日后启用长期记忆时，单独验证项目权限、撤权、来源、保留策略和对“关于我”的写入隔离。

## Global Constraints

- 单组织、项目权限边界；项目角色从当前数据库读取，不信任请求自带角色。
- 关于我的内容仅本人经过验证的人工会话可写，文件、对话、Agent、MCP、Worker、管理员无写权。
- 知识发布状态与工程完成状态分别存储；个人状态不随项目任务自动变化。
- 先本地 FakeAdapter，接真实工程资料前确认部署、身份、模型处理环境及保留政策。
- 首版不开放外部系统写入、任意 Shell、代码修改、发布或台架操作。
- 单文件 50 MiB、单批次 20 个文件、压缩展开上限 250 MiB，均可配置。
- 每代次最多 12 个模型动作步骤；单 Run 600 秒；单 Worker 并发 2；基础设施错误最多自动重试 2 次，累计 token / 费用预算不重置。
- 租约 60 秒、心跳 15 秒；fencing_token 必须单调增加。
- 审批默认 24 小时；默认 prepare_handoff 需 start_template_run 批准，其他两个模板不需要。
- 批准不扩大 ACL；自批、参数变更、策略版本变化或过期均不能启动执行。
- 未被有效知识 / 产物引用的原始或临时文件按 90 天策略清理；有效已发布知识及未归档问题固定保留来源。普通答案 / 产物默认 90 天，最后有效引用失效后来源再保留 90 天；审计默认 365 天。
- 周投入的 hours / percent、planned / actual 分开；未填实际不等于 0；手动进度百分比 0–100。
- 时间以 UTC 存储，默认 Asia/Shanghai 展示；变更使用乐观锁。

## Review Focus

1. 导入带宏、未知编码、压缩膨胀、修订或图像含义的文件，不可静默丢失或执行内容：任务 2、3。
2. 撤销资料访问后，旧答案、局部关联图、基线和产物不可泄露原内容：任务 4、5、6。
3. 自动化伪造 source=manual、后台同步、管理员修改以及部分项目可见的投入图，不可绕过个人边界：任务 1、4、6。
4. Worker 租约过期复活、取消竞态、重复领取或重启，不可重复执行或把失败标成成功：任务 2、7。
5. 90 天清理、批准后材料变化、缺少代理审批人与预算耗尽，不可破坏证据或自动放行：任务 4、7、8。

---

## 仓库结构与共用约定

下列路径为最初规划的职责示意，实际仓库结构可能不同；未实现的路径和模块仍属目标，不代表已经存在。

| 路径 | 职责 |
|---|---|
| AGENTS.md | 需求红线、完成标准、允许命令、验证顺序。 |
| contracts/*.schema.json | PersonalStatusInput、SourceLocator、TaskInput、ToolAction、RunCheckpoint、TaskResult。 |
| apps/api/pyproject.toml | API / Worker 包、锁定依赖和 pytest 配置。 |
| apps/api/workbench/app.py | 路由组合与生命周期，业务逻辑放功能模块。 |
| apps/api/workbench/identity.py、authz.py | Actor、认证、当前成员关系、资源级授权。 |
| apps/api/workbench/db.py、audit.py | 数据库事务、乐观锁、追加审计。 |
| apps/api/workbench/migrations/ | 每个任务附带的数据迁移。 |
| apps/api/workbench/projects/ | 项目和成员管理。 |
| apps/api/workbench/personal/ | 仅本人手动的状态、事项、投入和发布。 |
| apps/api/workbench/knowledge/ | 来源、提取、版本、关系、基线、检索和保留。 |
| apps/api/workbench/files/ | 文件预算、隔离存储、授权下载与转换。 |
| apps/api/workbench/questions/、issues/ | 问答证据、问题登记、状态迁移。 |
| apps/api/workbench/jobs/ | 通用领取、租约、心跳、任务状态、outbox。 |
| apps/api/workbench/agents/、approvals/ | 模板、Skill / 模型适配器、工具网关、启动审批。 |
| apps/api/tests/ | API、权限、持久状态与故障注入测试。 |
| apps/web/src/features/ | personal、projects、knowledge、questions、issues、tasks 的 UI。 |
| apps/web/tests/e2e/ | 浏览器闭环测试；只使用脱敏测试数据。 |
| skills/*/SKILL.md | 四个固定 Skill 的输入、输出、步骤和示例。 |
| fixtures/ | 脱敏文件、日志、知识、模型输出和失败样本。 |
| infra/compose.yaml | 本地 PostgreSQL、API、Web、Worker 的运行配置。 |

共用类型：Actor(user_id: UUID | None, principal_type: human_session | agent | worker)，ResourceRef(kind: str, id: UUID)。authorize(actor: Actor, action: str, resource: ResourceRef) -> None 失败抛出 Forbidden；本人检查在 PersonalStatusService 内再次执行。客户端 source / owner_id 无权改变 Actor。

HTTP 约定：401 未认证、403 无权限、404 在资源存在性需要隐藏时使用、409 版本冲突、413 超限、422 输入不合法。对象访问 404 / 403 的策略在 authz.py 一处定义；资源下载不存在和越权对不应知道该资源的用户都返回 404。

测试约定：任务 1 创建 client、sessions、seed_project、owner_status 的测试夹具，使用真实测试 PostgreSQL 与独立对象目录。任务 2 增加 controllable_clock、run_worker_once 和故障注入夹具。禁用真实外网、真实模型和真实内部凭证。

每个任务按红 → 绿执行；以下步骤和命令是目标验收方案，实际测试及其运行方式以当前仓库为准。

## Task 1：身份、项目与本人手动状态的首条闭环

**Files:**
- Create: AGENTS.md、infra/compose.yaml、apps/api/pyproject.toml、apps/api/workbench/app.py、identity.py、authz.py、db.py、audit.py。
- Create: apps/api/workbench/projects/models.py、service.py、routes.py；personal/models.py、schemas.py、service.py、routes.py。
- Create: apps/api/workbench/migrations/001_identity_projects_personal.py、contracts/personal-status-input.schema.json。
- Create: apps/web/package.json、src/features/personal/StatusPage.tsx、StatusEditor.tsx、AllocationChart.tsx、src/features/projects/ProjectSwitcher.tsx。
- Test: apps/api/tests/conftest.py、test_personal_boundary.py；apps/web/tests/e2e/personal.spec.ts。

**Interfaces:**
- Produces: authorize(Actor, action: str, ResourceRef) -> None。
- Produces: PersonalStatusService.save_draft(actor: Actor, payload: PersonalStatusInput, expected_version: int | None) -> StatusSnapshot；publish(actor: Actor, status_id: UUID, expected_version: int) -> StatusSnapshot。
- Produces: GET /api/personal/status；PUT /api/personal/status；POST /api/personal/status/{id}/publish。主体用户从认证上下文确定；不接受可改变所有者的请求字段。
- Produces: GET /api/projects；POST /api/projects；PUT /api/projects/{id}/members/{user_id}，仅授权管理者可改成员。

- [ ] **Step 1：先写权限和展示测试。** 定义 test_only_owner_human_session_can_save_and_publish：本人成功；另一同事、管理员、agent、worker 即使声明 source=manual 也失败。定义 test_stale_edit_returns_409、test_progress_out_of_range_rejected、test_allocations_keep_units_and_null_actual、test_hidden_project_not_in_status_totals。
- [ ] **Step 2：运行失败用例。** `uv run --project apps/api pytest apps/api/tests/test_personal_boundary.py -v`。预期红灯来自尚未实现的接口或行为；若启动配置错误，先修正配置使失败指向目标行为。
- [ ] **Step 3：实现上述接口与状态页。** 草稿、版本、发布快照和审计在事务中更新。前端提供专用手工表单，不提供 AI 生成或文件导入入口。可视化仅读取授权内容，明确“截至手动更新时间”，不混合投入单位。
- [ ] **Step 4：验证闭环。** 上述 pytest 全绿；`pnpm --dir apps/web exec playwright test tests/e2e/personal.spec.ts` 全绿。浏览器用例覆盖本人保存、发布、同事只读和隐藏项目不泄露。
- [ ] **Step 5：提交任务 1。** 附验证结果和剩余环境假设，提交信息建议 `feat: add owner-only manual work status`。

## Task 2：Markdown / TXT 后台导入与人工发布

**Files:**
- Create: apps/api/workbench/files/storage.py、limits.py、routes.py；knowledge/models.py、sources.py、publishing.py、parsers/text.py、routes.py。
- Create: apps/api/workbench/jobs/models.py、queue.py、worker.py、outbox.py；migrations/002_knowledge_sources_jobs.py。
- Create: contracts/source-locator.schema.json；apps/web/src/features/knowledge/ImportPage.tsx、ImportPreview.tsx。
- Test: apps/api/tests/test_text_import.py、test_job_claim.py；fixtures/knowledge.md、knowledge-gbk.txt、unsafe-markdown.md。

**Interfaces:**
- Consumes: Actor、authorize、项目与对象访问规则。
- Produces: ImportService.submit(actor: Actor, project_id: UUID, files: list[UploadRef], metadata: ImportMetadata) -> list[UUID]；publish_batch(actor: Actor, version_ids: list[UUID]) -> list[KnowledgeVersionRef]。
- Produces: JobQueue.claim(worker_id: str, now: datetime) -> Lease | None；heartbeat(lease: Lease, now: datetime) -> None；Lease(job_id: UUID, token: int, expires_at: datetime)。
- Produces: POST /api/projects/{id}/imports；GET /api/imports/{id}；POST /api/knowledge/publish-batch。

- [ ] **Step 1：先写行为测试。** test_upload_returns_persisted_job、test_only_published_chunks_searchable、test_locator_matches_original_lines、test_encoding_requires_confirmation_when_ambiguous、test_file_and_batch_limits、test_markdown_does_not_execute_script_or_fetch_url、test_duplicate_claim_has_one_valid_lease、test_import_cannot_target_personal_status。
- [ ] **Step 2：运行红灯。** `uv run --project apps/api pytest apps/api/tests/test_text_import.py apps/api/tests/test_job_claim.py -v`。
- [ ] **Step 3：实现原始存储、持久作业、解析、预览和发布。** Task / Job 与 outbox 事务写入；已发布正文不可原地改。默认无外网。发布动作批量确认整批来源和知识，不要求逐片段确认。
- [ ] **Step 4：运行上述测试并演示关闭页面后导入仍完成。** 数据库重启后仍可读取作业 ID；使用独立 Worker 而非 Web 进程内线程作为完成依据。
- [ ] **Step 5：提交任务 2。** 建议 `feat: import and publish project text knowledge`。

## Task 3：Office、对话导入与来源预览

**Files:**
- Create: apps/api/workbench/knowledge/parsers/word.py、slides.py、conversation.py；files/converter.py；migrations/003_rich_sources.py。
- Create: apps/web/src/features/knowledge/SourcePreview.tsx、ConversationCapture.tsx。
- Test: apps/api/tests/test_rich_import.py；fixtures/rich/ 下的 document.docx、slides.pptx、legacy.doc、legacy.ppt、tracked.docx、encrypted.docx、expansion-limit.docx、conversation.json。

**Interfaces:**
- Consumes: ImportService、SourceLocator、JobQueue、已存在的发布流程。
- Produces: parse_source(source: SourceVersionRef, options: ParseOptions) -> ParsedSource，输出 blocks、locators、images、warnings；ParsedSource 不自行发布。
- Produces: POST /api/projects/{id}/conversation-captures，body 包含用户明确选定的消息快照及关联项目知识信息；只能创建知识草稿。

- [ ] **Step 1：先写格式边界测试。** test_docx_table_locator、test_unresolved_revisions_block_publication、test_slide_notes_require_selection、test_slide_images_retained、test_ocr_candidate_requires_confirmation、test_legacy_conversion_keeps_source_hash、test_missing_converter_is_actionable、test_encrypted_and_expansion_limit_fail_safely、test_conversation_capture_does_not_write_personal。
- [ ] **Step 2：运行红灯。** `uv run --project apps/api pytest apps/api/tests/test_rich_import.py -v`。
- [ ] **Step 3：实现统一解析契约与隔离转换。** 转换进程禁宏、无外网、受时间和展开空间限制。用原始 / 转换摘要和定位映射保留来源。首版图像解释可通过人工输入或可选批准的图像适配器完成；未配置时保留图像与待处理标志，不伪造 OCR。
- [ ] **Step 4：运行上述测试。** Office 预览以原文件 / 页面和提取正文对照；旧格式既测试有转换器的成功路径，也测试缺失路径，试点完整验收要求安装获准转换器。
- [ ] **Step 5：提交任务 3。** 建议 `feat: add office and conversation knowledge intake`。

## Task 4：项目可视化、版本、基线与证据保留

**Files:**
- Create: apps/api/workbench/knowledge/relations.py、baselines.py、impact.py、retention.py；migrations/004_traceability_baselines.py。
- Create: apps/web/src/features/knowledge/KnowledgeTree.tsx、EntryList.tsx、EntryDetail.tsx、RequirementGraph.tsx、MissingLinksView.tsx、VersionCompare.tsx。
- Test: apps/api/tests/test_traceability.py、test_retention.py；apps/web/tests/e2e/knowledge.spec.ts。

**Interfaces:**
- Consumes: KnowledgeVersionRef、SourceVersionRef、authorize。
- Produces: get_trace(actor: Actor, entry_id: UUID, baseline_id: UUID | None) -> TraceGraph；create_baseline(actor: Actor, project_id: UUID, label: str) -> UUID；mark_dependents_for_review(version_id: UUID) -> list[UUID]。
- Produces: retention_candidates(now: datetime) -> list[SourceVersionRef]，只返回无有效证据引用且超过保留期的来源；withdraw_source(actor: Actor, source_version_id: UUID, reason: str) -> WithdrawalReport。
- Produces: GET /api/knowledge/{id}/trace；POST /api/projects/{id}/baselines；GET /api/projects/{id}/missing-links。

- [ ] **Step 1：先写追溯与清理测试。** test_design_can_link_multiple_requirements、test_baseline_does_not_follow_head、test_upstream_change_only_marks_review、test_hidden_nodes_and_edges_not_counted、test_published_source_survives_day_91、test_unpinned_source_expires_after_90_days、test_withdrawal_blocks_dependents_and_shows_baseline_gap。
- [ ] **Step 2：运行红灯。** `uv run --project apps/api pytest apps/api/tests/test_traceability.py apps/api/tests/test_retention.py -v`。
- [ ] **Step 3：实现关联、版本比较、冻结基线及保留清理。** 清理先计算依赖再删除，强制撤销先阻断依赖访问。关系建议与确认分开；局部图以当前条目为中心展开。工程完成状态由人工维护，不由知识发布推断。
- [ ] **Step 4：运行上述测试与 `pnpm --dir apps/web exec playwright test tests/e2e/knowledge.spec.ts`。** 覆盖 320px 与常规桌面宽度的导航、详情及关联选择；缺项显示为“未关联”。
- [ ] **Step 5：提交任务 4。** 建议 `feat: visualize versioned project traceability`。

## Task 5：有出处的授权问答与个人状态只读引用

**Files:**
- Create: apps/api/workbench/questions/models.py、service.py、evidence.py、routes.py；knowledge/retrieval.py；agents/model_adapter.py、fake_adapter.py；migrations/005_questions.py。
- Create: apps/web/src/features/questions/QuestionPage.tsx、AnswerEvidence.tsx；skills/answer-approved-knowledge/SKILL.md。
- Test: apps/api/tests/test_grounded_answers.py；fixtures/answers/ 下的 grounded.json、missing-evidence.json、injected-source.md。

**Interfaces:**
- Consumes: 授权已发布知识、基线、SourceLocator、PersonalStatusService 的只读快照。
- Produces: ModelAdapter.respond(request: ModelRequest) -> ModelResponse，FakeAdapter 按 fixture 返回，不访问外网。
- Produces: QuestionService.ask(actor: Actor, question: str, project_id: UUID | None, baseline_id: UUID | None, scope: project | personal) -> AnswerSnapshot。
- Produces: validate_answer(answer: AnswerDraft, allowed_evidence: list[EvidenceRef]) -> ValidationReport；POST /api/questions。

- [ ] **Step 1：先写依据和撤权测试。** test_draft_and_retired_excluded、test_citation_must_match_source_locator、test_no_evidence_returns_gap、test_baseline_is_explicit、test_source_instructions_do_not_change_tools、test_revoked_membership_blocks_old_answer_and_file、test_personal_answer_uses_only_published_visible_snapshot_and_timestamp。
- [ ] **Step 2：运行红灯。** `uv run --project apps/api pytest apps/api/tests/test_grounded_answers.py -v`。
- [ ] **Step 3：先实现全文检索与 FakeAdapter 回答。** 引用和答案在展示时复核来源 ACL；撤销后保留审计而隐藏不可访问正文。对话生成个人状态修改请求一律不执行，提供本人手工编辑页面导航即可。
- [ ] **Step 4：运行上述测试。** 问答测试检查行为与证据，不按整段生成文字做脆弱快照；个人问答必须注明手动更新时点。
- [ ] **Step 5：提交任务 5。** 建议 `feat: answer from authorized published evidence`。

## Task 6：问题登记与三类后台任务的模拟闭环

**Files:**
- Create: apps/api/workbench/issues/models.py、service.py、routes.py；jobs/tasks.py、task_routes.py；agents/templates.py、runner.py、tool_gateway.py、validators.py；migrations/006_issues_tasks_runs.py。
- Create: contracts/task-input.schema.json、tool-action.schema.json、run-checkpoint.schema.json、task-result.schema.json。
- Create: apps/web/src/features/issues/IssuePage.tsx、IssueForm.tsx；tasks/TaskSubmit.tsx、TaskDetail.tsx、ResultReview.tsx。
- Create: skills/analyze-build-log/SKILL.md、triage-mde-issue/SKILL.md、prepare-handoff/SKILL.md。
- Test: apps/api/tests/test_issue_task_flow.py；fixtures/logs/build-failure.log；apps/web/tests/e2e/task-flow.spec.ts。

**Interfaces:**
- Consumes: JobQueue、ModelAdapter、authorize、来源定位与审计。
- Produces: IssueService.from_question(actor: Actor, answer_id: UUID, payload: IssueInput) -> IssueSnapshot；transition(actor: Actor, issue_id: UUID, target: str, reason: str, expected_version: int) -> IssueSnapshot。
- Produces: TaskService.submit(actor: Actor, template_id: str, payload: TaskInput) -> TaskSnapshot；cancel(actor: Actor, task_id: UUID, expected_version: int) -> TaskSnapshot；review(actor: Actor, task_id: UUID, decision: accepted | changes_requested, comment: str) -> TaskSnapshot。
- Produces: ToolGateway.execute(context: ExecutionContext, action: ToolAction) -> ToolObservation。ExecutionContext 包含真实 Actor、项目、代次、lease、策略及预算，不来自模型。
- Produces: POST /api/issues；POST /api/tasks；GET /api/tasks/{id}；POST /api/tasks/{id}/cancel；POST /api/tasks/{id}/review。

- [ ] **Step 1：先写闭环测试。** test_question_to_issue_keeps_evidence、test_all_three_templates_validate_result、test_bad_evidence_cannot_reach_awaiting_review、test_accepting_task_does_not_close_issue、test_task_success_does_not_change_personal_status、test_artifact_access_intersects_source_acl、test_forbidden_tool_never_executes。
- [ ] **Step 2：运行红灯。** `uv run --project apps/api pytest apps/api/tests/test_issue_task_flow.py -v`。
- [ ] **Step 3：实现模拟执行和审阅。** 假模型覆盖正常与失败路径；日志证据使用行号或记录 ID。结果先验证再 awaiting_review，接受或返工由有权人员执行。prepare_handoff 在任务 7 的审批完成前保持 awaiting_approval，不临时放开策略。
- [ ] **Step 4：运行上述测试及 `pnpm --dir apps/web exec playwright test tests/e2e/task-flow.spec.ts`。** 浏览器测试先覆盖无需启动审批的日志任务，关闭提交页面后从任务中心读取结果。
- [ ] **Step 5：提交任务 6。** 建议 `feat: add structured issues and bounded background tasks`。

## Task 7：真实启动审批、恢复、取消与幂等

**Files:**
- Create: apps/api/workbench/approvals/models.py、service.py、routes.py；jobs/recovery.py、state_machine.py、budget.py；migrations/007_approvals_recovery.py。
- Modify: apps/api/workbench/jobs/queue.py、worker.py、tasks.py；agents/tool_gateway.py。
- Create: apps/web/src/features/tasks/ApprovalPanel.tsx、RunTimeline.tsx。
- Test: apps/api/tests/test_run_approvals.py、test_worker_recovery.py、test_cancellation.py；apps/web/tests/e2e/approval-flow.spec.ts。

**Interfaces:**
- Consumes: TaskSnapshot、ExecutionContext、Lease、输入 / 策略摘要、当前 ACL。
- Produces: ApprovalService.decide(actor: Actor, approval_id: UUID, decision: approved | rejected, expected_version: int) -> ApprovalSnapshot；authorize_generation(task_id: UUID, approval_id: UUID, now: datetime) -> ExecutionGeneration，原子消费。
- Produces: recover_expired_jobs(now: datetime) -> list[UUID]；TaskStateMachine.transition(task_id: UUID, expected_version: int, event: TaskEvent) -> TaskSnapshot。
- Produces: POST /api/approvals/{id}/decision；GET /api/tasks/{id}/runs。

- [ ] **Step 1：先写审批与故障测试。** test_handoff_waits_for_distinct_approver、test_self_approval_rejected、test_missing_approver_expires_after_24h、test_changed_input_or_policy_invalidates_approval、test_two_approvals_cannot_start_twice、test_retry_reuses_generation_and_budget、test_explicit_retry_requires_new_gate。
- [ ] **Step 2：补上运行故障红灯。** test_old_lease_fenced、test_restart_resumes_after_checkpoint、test_cancel_race_prevents_next_tool、test_cancel_does_not_claim_rollback、test_retry_limit_is_two、test_run_timeout_at_600s、test_unknown_side_effect_needs_reconciliation。运行 `uv run --project apps/api pytest apps/api/tests/test_run_approvals.py apps/api/tests/test_worker_recovery.py apps/api/tests/test_cancellation.py -v`。
- [ ] **Step 3：实现服务端状态机和原子审批。** 审批等待不领取 Worker；执行前重新检查当前权限；失效租约无法调用工具。对内部完成事件幂等写入，旧 Run 不覆盖，新代次恢复预算规则按 Spec。
- [ ] **Step 4：运行上述测试及 `pnpm --dir apps/web exec playwright test tests/e2e/approval-flow.spec.ts`。** 使用两个独立账号走“同事请求交接 → 负责人批准 → 运行 → 证据验证 → 人工接受”；再注入中断和取消，不依赖真实等待 24 小时。
- [ ] **Step 5：提交任务 7。** 建议 `feat: enforce approvals and durable task recovery`。

## Task 8：获准模型、只读适配器与试点发布检查

**Files:**
- Create: apps/api/workbench/agents/provider_adapter.py、mcp_gateway.py、configuration.py；knowledge/evaluation.py。
- Create: fixtures/evaluation/questions.jsonl、logs.jsonl、issues.jsonl；docs/runbook.md、data-boundaries.md、evaluation-results.md。
- Test: apps/api/tests/test_provider_contract.py、test_gateway_identity.py、test_release_guards.py；apps/web/tests/e2e/smoke.spec.ts。

**Interfaces:**
- Consumes: ModelAdapter、ToolGateway、现有 Schema 和服务端 Actor。
- Produces: ProviderAdapter.respond(ModelRequest) -> ModelResponse，与 FakeAdapter 一致；MCPGateway.call(context: ExecutionContext, tool_name: str, arguments: dict) -> ToolObservation，只读白名单。
- Produces: validate_release_configuration(settings: Settings) -> ValidationReport，真实部署拒绝假身份、缺少来源存储或未批准模型配置。

- [ ] **Step 1：先写适配器与上线门禁测试。** test_model_output_must_match_schema、test_bad_locator_rejected、test_token_cap_stops_execution、test_mcp_preserves_requester_scope、test_no_token_passthrough、test_dev_identity_rejected_in_production、test_missing_approved_environment_blocks_live_mode。
- [ ] **Step 2：用模拟服务跑红灯。** `uv run --project apps/api pytest apps/api/tests/test_provider_contract.py apps/api/tests/test_gateway_identity.py apps/api/tests/test_release_guards.py -v`。不在本步骤发送真实工程数据。
- [ ] **Step 3：在获准后实现真实适配器并保留 FakeAdapter。** 先核对官方文档和组织凭证；配置每个工具的身份、资源、网络和预算。没有批准环境时保持 live_enabled=false，交付完整本地模拟版本，不绕过该边界。
- [ ] **Step 4：执行适配器测试和最终 smoke。** `pnpm --dir apps/web exec playwright test tests/e2e/smoke.spec.ts`；用脱敏集记录 20 问题、10 日志、10 问题单的人工评测及失败原因。只在变更引入跨模块风险时运行完整回归，不无依据反复重跑。
- [ ] **Step 5：提交任务 8 并形成具体试点部署包。** 验证备份恢复、保留清理、日志脱敏及停用模型的操作手册。实际部署与接入真实数据是独立发布动作，按用户及组织授权执行。

## 最终 smoke 的完整路径

1. 本人手动填写、发布本周工作状态；同事可读，自动化写入失败。
2. 项目 A 上传 Markdown、Word、PPT、TXT 并选取对话片段；预览后发布。
3. 把需求、分析、设计和实现关联；创建基线；在局部图中展开并查看来源。
4. 同事问答获得可核对引用；项目 B 成员无法访问项目 A 的知识和来源。
5. 无法解决的问题转问题单；提交日志任务；关闭页面后仍完成并等待人工验收。
6. 交接任务执行前等待指定人员批准；参数变化后原批准不可用。
7. 注入 Worker 中断、租约过期和取消，确认没有重复副作用或误报成功。
8. 撤销来源权限，历史答案与产物无法绕过；有效知识的证据不被 90 天清理破坏。

## 计划自检与执行交接

- R01 → 任务 5、6、7；R02 → 任务 2、3；R03 / R04 → 任务 4；R05 / R06 → 任务 1，并在任务 3、5、6 回归；R07 → 任务 1、2、5、7、8。
- 全部跨任务接口均以上文 Interfaces 为准；若执行中必须改变签名，先同步合同与消费者，再继续，不能各模块自行改名。
- 不把当前文档校验冒充应用测试；各任务的红 / 绿结果及 smoke 报告由未来执行者保存。
- 建议先按本会话顺序执行，首个可演示交付是任务 1，随后交付项目知识闭环。本文不自动开启多个开发 Agent，也不默认为已经批准发布或接入内部系统。
- 进入产品代码实现前核对这份具体设计与计划；当前待确定的是生产数据 / 模型 / 身份 / 保留环境，本地模拟开发不依赖这些信息。

