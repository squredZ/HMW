import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'

type Project = { id: string; name: string }
type Analysis = { state: string; report?: string | null; reason?: string | null }
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
    if (!projects.some(item => item.id === project) || !module.trim() || !description.trim() || !log) return
    setBusy(true); setMessage('')
    try {
      const body = new FormData()
      body.append('project_id', project); body.append('module', module.trim()); body.append('description', description.trim()); body.append('log', log)
      const response = await fetch('/api/guest/issues', {method:'POST', credentials:'same-origin', body})
      if (!response.ok) throw new Error(response.status === 401 ? '临时会话已失效，请刷新页面。' : response.status === 413 ? '日志文件不能超过 10 MB。' : response.status === 409 ? '提交发生冲突，请刷新页面后重试。' : '提交失败，请确认项目仍可访问后重试。')
      setModule(''); setDescription(''); setLog(null); setFileKey(value => value + 1); setMessage('问题已提交。')
      await loadIssues()
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
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
      <p>{issue.description || '未填写问题现象'}</p><p>日志：{issue.log_filename || '无'} · 状态：{issue.state || '未知'}</p>
      {issue.analysis && <><p>分析状态：{issue.analysis.state === 'queued' ? '排队中' : issue.analysis.state === 'running' ? '分析中' : issue.analysis.state === 'failed' ? '分析失败' : issue.analysis.state === 'completed' ? '分析完成' : issue.analysis.state}</p>
        {issue.analysis.report && <pre>{issue.analysis.report}</pre>}{issue.analysis.state === 'failed' && <p>失败原因：{(issue.analysis.reason && analysisReasons.get(issue.analysis.reason)) || '分析失败，请联系负责人检查后重试。'}</p>}</>}
      {!issue.log_filename && <p>该问题没有日志，无法分析。</p>}
      <button type="button" className="secondary" disabled={analyzing !== null || isActive(issue.analysis) || !issue.log_filename} onClick={()=>void analyze(issue.id)}>分析问题</button>
    </article>)}
    <div className="actions"><button type="button" className="secondary" disabled={page === 0 || busy || analyzing !== null} onClick={()=>{setList(null); setPage(value => value - 1)}}>上一页</button><span>第 {page + 1} 页</span><button type="button" className="secondary" disabled={!list?.has_more || busy || analyzing !== null} onClick={()=>{setList(null); setPage(value => value + 1)}}>下一页</button></div>
  </section>
}
