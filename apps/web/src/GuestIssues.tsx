import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { issueStateLabels } from './issueStates'

type Project = { id: string; name: string }
type Usage = { input: number; output: number; cache_read: number; cache_write: number; total: number; partial?: boolean }
type Analysis = { id?: string; state: string; report?: string | null; reason?: string | null; usage?: Usage | null; created_at?: number }
type Issue = { id: string; project_id?: string; project_name?: string; module?: string | null; title?: string | null; description?: string | null; log_filename?: string | null; state?: string; analysis?: Analysis | null }
type IssueList = { items: Issue[]; has_more: boolean }

const analysisReasons = new Map<string, string>([
  ['model_not_configured', '分析模型未配置，请联系负责人配置模型。'],
  ['code_root_unavailable', '项目代码目录不可用，请联系负责人检查配置。'],
  ['skill_root_unavailable', '技能目录不可用，请联系负责人检查配置。'],
  ['log_integrity_failed', '日志校验失败，请重新提交问题和日志。'],
  ['context_limit', '分析内容超过模型上下文限制，请缩小日志范围后重试。'],
  ['timeout', '分析超时，请稍后重试。'],
  ['resource_unavailable', '分析所需资源不可用，请联系负责人检查。'],
])

const isActive = (analysis?: Analysis | null) => analysis?.state === 'queued' || analysis?.state === 'running'

function submissionKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`
}

function AnalysisHistory({ issueId }: { issueId: string }) {
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(0)
  const [result, setResult] = useState<{items: Analysis[]; has_more: boolean} | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setResult(null); setError('')
    void fetch(`/api/guest/issues/${encodeURIComponent(issueId)}/analyses?limit=20&offset=${page * 20}`, {credentials:'same-origin', signal:controller.signal})
      .then(response => { if (!response.ok) throw new Error(); return response.json() })
      .then(value => { if (!controller.signal.aborted) setResult(value) })
      .catch(() => { if (!controller.signal.aborted) setError('分析记录加载失败，请关闭后重试。') })
    return () => controller.abort()
  }, [issueId, open, page])
  return <div><button type="button" className="secondary" onClick={() => setOpen(value => !value)}>{open ? '收起分析记录' : '查看分析记录'}</button>
    {open && <div>{error && <p role="status">{error}</p>}{result && <>
      {result.items.map(item => <div key={item.id}><p>分析时间：{item.created_at != null ? new Date(item.created_at * 1000).toLocaleString() : '未知'} · 状态：{item.state}</p>
        <p>{item.usage ? `Token 用量${item.usage.partial ? '（部分用量，至少）' : ''}：输入 ${item.usage.input}，输出 ${item.usage.output}，缓存读取 ${item.usage.cache_read}，缓存写入 ${item.usage.cache_write}，总计 ${item.usage.total}` : 'Token 用量：未知'}</p>
        {item.report && <pre>{item.report}</pre>}</div>)}
      <button type="button" disabled={page === 0} onClick={() => setPage(value => value - 1)}>分析记录上一页</button>
      <span>第 {page + 1} 页分析记录</span>
      <button type="button" disabled={!result.has_more} onClick={() => setPage(value => value + 1)}>分析记录下一页</button>
    </>}</div>}
  </div>
}

export default function GuestIssues({ projects }: { projects: Project[] }) {
  const [project, setProject] = useState('')
  const [module, setModule] = useState('')
  const [description, setDescription] = useState('')
  const [log, setLog] = useState<File | null>(null)
  const [fileKey, setFileKey] = useState(0)
  const [message, setMessage] = useState('')
  const [listError, setListError] = useState('')
  const [pollAttempt, setPollAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const [analyzing, setAnalyzing] = useState<string | null>(null)
  const [list, setList] = useState<IssueList | null>(null)
  const [page, setPage] = useState(0)
  const request = useRef<AbortController | null>(null)
  const submission = useRef<{key:string; project:string; module:string; description:string; log:File} | null>(null)
  const submitting = useRef(false)

  const loadIssues = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    try {
      const response = await fetch(page === 0 ? '/api/guest/issues' : `/api/guest/issues?limit=50&offset=${page * 50}`, {credentials:'same-origin', signal:controller.signal})
      if (!response.ok) throw new Error(response.status === 401 ? '临时会话已失效，请刷新页面。' : '问题列表加载失败，请重试。')
      const result: IssueList = await response.json()
      if (!controller.signal.aborted) { setList(result); setListError('') }
    } catch (error) {
      if (!controller.signal.aborted) { setListError((error as Error).message); setPollAttempt(value => value + 1) }
    }
  }, [page])

  useEffect(() => {
    void loadIssues()
    return () => request.current?.abort()
  }, [loadIssues])

  useEffect(() => {
    if (!list?.items.some(issue => isActive(issue.analysis))) return
    const timer = window.setTimeout(() => { void loadIssues() }, 2000)
    return () => window.clearTimeout(timer)
  }, [list, pollAttempt, loadIssues])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (submitting.current || !projects.some(item => item.id === project) || !module.trim() || !description.trim() || !log) return
    const current = submission.current
    if (!current || current.project !== project || current.module !== module.trim() ||
        current.description !== description.trim() || current.log !== log) {
      submission.current = {key:submissionKey(), project, module:module.trim(), description:description.trim(), log}
    }
    submitting.current = true
    setBusy(true); setMessage('')
    try {
      const body = new FormData()
      body.append('project_id', project); body.append('module', module.trim()); body.append('description', description.trim()); body.append('log', log)
      const response = await fetch('/api/guest/issues', {method:'POST', credentials:'same-origin', headers:{'Idempotency-Key':submission.current!.key}, body})
      if (!response.ok) throw new Error(response.status === 401 ? '临时会话已失效，请刷新页面。' : response.status === 413 ? '日志文件不能超过 10 MB。' : response.status === 409 ? '提交发生冲突，请刷新页面后重试。' : '提交失败，请确认项目仍可访问后重试。')
      submission.current = null
      setModule(''); setDescription(''); setLog(null); setFileKey(value => value + 1); setMessage('问题已提交。')
      await loadIssues()
    } catch (error) { setMessage(error instanceof TypeError ? '网络异常，提交结果未知，请直接重试。' : (error as Error).message) }
    finally { submitting.current = false; setBusy(false) }
  }

  async function analyze(id: string) {
    setAnalyzing(id); setMessage('')
    try {
      const response = await fetch(`/api/guest/issues/${encodeURIComponent(id)}/analyze`, {method:'POST', credentials:'same-origin'})
      if (!response.ok) throw new Error(response.status === 401 ? '临时会话已失效，请刷新页面。' : response.status === 409 ? '问题缺少日志，或分析已在排队或运行中，请刷新列表。' : '无法提交分析，请稍后重试。')
      await loadIssues()
    } catch (error) { setMessage((error as Error).message) } finally { setAnalyzing(null) }
  }

  return <section className="card"><h2>登记问题</h2><p>选择项目，描述问题并上传日志。问题列表对所有访客可见。</p>
    {projects.length === 0 && <p>暂无可提交问题的项目，请联系负责人创建项目。</p>}
    <form onSubmit={submit}><label>问题所属项目<select value={projects.some(item => item.id === project) ? project : ''} disabled={busy} onChange={event=>setProject(event.target.value)}><option value="">选择项目</option>{projects.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>功能模块<input value={module} maxLength={200} disabled={busy} onChange={event=>setModule(event.target.value)} /></label>
      <label>问题现象<textarea value={description} maxLength={10000} rows={6} disabled={busy} onChange={event=>setDescription(event.target.value)} placeholder="描述现象、预期结果和必要的复现步骤" /></label>
      <p>支持日志文件及 zip、tar、tar.gz、rar 压缩包，大小不超过 10 MB。</p><label>日志文件<input key={fileKey} type="file" disabled={busy} onChange={event=>setLog(event.target.files?.[0] ?? null)} /></label>
      <button disabled={busy || !projects.some(item => item.id === project) || !module.trim() || !description.trim() || !log}>提交问题</button>
    </form>{message && <p role="status">{message}</p>}
    <h2>共享问题列表</h2><button type="button" className="secondary" onClick={()=>void loadIssues()}>刷新问题</button>
    {listError && <p role="status">{listError}</p>}
    {list && list.items.length === 0 && <p>暂无问题。</p>}
    {list?.items.map(issue => <article key={issue.id}><h3>{issue.project_name || issue.project_id || '未知项目'} · {issue.module || issue.title || '未填写模块'}</h3>
      <p>{issue.description || '未填写问题现象'}</p><p>日志：{issue.log_filename || '无'} · 状态：{issueStateLabels[issue.state ?? ''] ?? '未知'}</p>
      {issue.analysis && <><p>分析状态：{issue.analysis.state === 'queued' ? '排队中' : issue.analysis.state === 'running' ? '分析中' : issue.analysis.state === 'failed' ? '分析失败' : issue.analysis.state === 'completed' ? '分析完成' : issue.analysis.state}</p>
        {issue.analysis.report && <pre>{issue.analysis.report}</pre>}{issue.analysis.state === 'failed' && <p>失败原因：{(issue.analysis.reason && analysisReasons.get(issue.analysis.reason)) || '分析失败，请联系负责人检查后重试。'}</p>}</>}
      {!issue.log_filename && <p>该问题没有日志，无法分析。</p>}
      <button type="button" className="secondary" disabled={analyzing !== null || isActive(issue.analysis) || !issue.log_filename} onClick={()=>void analyze(issue.id)}>分析问题</button>
      {issue.analysis && <AnalysisHistory issueId={issue.id} />}
    </article>)}
    <div className="actions"><button type="button" className="secondary" disabled={page === 0 || busy || analyzing !== null} onClick={()=>{setList(null); setPage(value => value - 1)}}>上一页</button><span>第 {page + 1} 页</span><button type="button" className="secondary" disabled={!list?.has_more || busy || analyzing !== null} onClick={()=>{setList(null); setPage(value => value + 1)}}>下一页</button></div>
  </section>
}
