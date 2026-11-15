import { useState } from 'react'

type Project = { id: string; name: string; role: string }
type Draft = { id: string; state: string; content: string; locator: { start_line: number; end_line: number }; source: { id: string; filename: string } }
type Job = { state: string; result: { knowledge_id?: string; reason?: string } | null }

async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {credentials: 'same-origin', ...options})
  if (!response.ok) throw new Error(`操作失败（${response.status}）`)
  return response.json()
}

export default function KnowledgeImport({ csrf, projects }: { csrf: string; projects: Project[] }) {
  const [project, setProject] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [jobId, setJobId] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit() {
    if (!project || !file) return
    setBusy(true); setMessage(''); setDraft(null); setJobId('')
    try {
      const form = new FormData()
      form.append('files', file)
      const result = await api<{jobs:string[]}>(`/api/projects/${encodeURIComponent(project)}/imports`, {method:'POST', headers:{'X-CSRF-Token':csrf}, body:form})
      setJobId(result.jobs[0]); sessionStorage.setItem(`hmw.import.${project}`, result.jobs[0]); setMessage('导入任务已提交')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function refresh() {
    if (!jobId) return
    setBusy(true); setMessage(''); setDraft(null)
    try {
      const job = await api<Job>(`/api/imports/${encodeURIComponent(jobId)}`)
      if (job.state === 'completed' && job.result?.knowledge_id) {
        setDraft(await api<Draft>(`/api/knowledge/${encodeURIComponent(job.result.knowledge_id)}`))
      } else if (job.state === 'needs_input') {
        setMessage('无法识别文件编码，请转换为 UTF-8 后重新上传。')
      } else if (job.state === 'failed') {
        setMessage(`导入失败（${job.result?.reason ?? '未知原因'}）`)
      } else {
        setMessage('任务尚在处理，请稍后刷新。')
      }
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function publish() {
    if (!draft) return
    setBusy(true); setMessage('')
    try {
      await api('/api/knowledge/publish-batch', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({version_ids:[draft.id]})})
      setDraft({...draft, state:'published'}); setMessage('知识已发布')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  return <section className="card knowledge-import"><h2>导入项目知识</h2><p>支持 UTF-8 Markdown 和 TXT，先预览草稿再发布。</p>
    <label>导入项目<select value={project} onChange={event=>{setProject(event.target.value); setJobId(sessionStorage.getItem(`hmw.import.${event.target.value}`) ?? ''); setDraft(null); setMessage('')}}><option value="">选择项目</option>{projects.filter(item=>item.role === 'owner' || item.role === 'maintainer').map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>选择知识文件<input type="file" accept=".md,.txt,text/plain,text/markdown" onChange={event=>{setFile(event.target.files?.[0] ?? null); setJobId(''); setDraft(null); setMessage('')}} /></label>
    <div className="actions"><button type="button" onClick={submit} disabled={busy || !project || !file}>提交导入</button><button type="button" className="secondary" onClick={refresh} disabled={busy || !jobId}>刷新任务</button></div>
    {message && <p role="status">{message}</p>}
    {draft && <div><h3>来源：{draft.source.filename}</h3><a href={`/api/sources/${encodeURIComponent(draft.source.id)}/download`}>下载原文件</a><p>行 {draft.locator.start_line}–{draft.locator.end_line} · {draft.state === 'published' ? '已发布' : '草稿'}</p><pre>{draft.content}</pre><button type="button" onClick={publish} disabled={busy || draft.state !== 'draft'}>发布知识</button></div>}
  </section>
}
