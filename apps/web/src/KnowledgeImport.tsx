import { useEffect, useState } from 'react'
import SkillManager from './SkillManager'

type Project = { id: string; name: string; role: string }
type Draft = { id: string; state: string; module?:string; guest_visible?: boolean; content: string; locator: { start_line?: number; end_line?: number; kind?: 'docx' | 'pptx'; anchors?: {line:number; kind?:string; index?:number; slide?:number; element?:number}[]; warnings?: string[] }; source: { id: string; filename: string } }
type Job = { state: string; result: { knowledge_id?: string; reason?: string } | null }
type JobSummary = {id: string; filename: string; state: string; created_at: string}

export const reviewWarnings: Record<string, string> = {comments_present:'原文件包含批注，请人工核对。', images_require_review:'原文件包含图片，请下载原件核对图片内容。'}
const inputMessages: Record<string, string> = {encoding_unrecognized:'无法识别文件编码，请转换为 UTF-8 后重新上传。', converter_unavailable:'暂不支持旧版 Office 转换，请转换为 DOCX 或 PPTX 后重新上传。', unresolved_revisions:'文档包含未解决的修订，请确认最终内容后重新上传。', expansion_limit:'文件展开后超出大小限制，请缩小文件后重新上传。', encrypted_file:'文件受密码保护，请提供可读取的副本。', encrypted_or_unsupported:'文件可能受密码保护或格式不受支持，请提供可读取的副本。', invalid_office_file:'无法读取此 Office 文件，请检查文件后重新上传。', no_extractable_text:'没有可提取的文本，请检查原文件。', macros_not_supported:'暂不支持含宏的文件，请提供不含宏的副本。', unsafe_xml:'文件包含不支持的 XML 内容，请检查后重新上传。'}

const jobLabels: Record<string, string> = {queued:'等待处理', running:'处理中', completed:'已完成', needs_input:'待补材料', failed:'失败'}

async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {credentials: 'same-origin', ...options})
  if (!response.ok) throw new Error(`操作失败（${response.status}）`)
  return response.json()
}

export default function KnowledgeImport({ csrf, projects }: { csrf: string; projects: Project[] }) {
  const [project, setProject] = useState('')
  const [module, setModule] = useState('公共知识')
  const [modules, setModules] = useState<string[]>(['公共知识'])
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [tracking, setTracking] = useState(false)
  const [pollCount, setPollCount] = useState(0)
  const [jobId, setJobId] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [guestVisible, setGuestVisible] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState<JobSummary[]>([])

  useEffect(() => {
    if (!tracking || !jobId || busy || pollCount >= 20) return
    const timer = window.setTimeout(() => {
      setPollCount(count => count + 1)
      void refresh(jobId)
    }, 3000)
    return () => window.clearTimeout(timer)
  }, [tracking, jobId, busy, pollCount])

  async function submit(upload: File | null) {
    if (!project || !upload || !module.trim()) return
    setBusy(true); setMessage(''); setDraft(null); setJobId('')
    try {
      const form = new FormData()
      form.append('files', upload)
      form.append('module', module.trim())
      const result = await api<{jobs:string[]}>(`/api/projects/${encodeURIComponent(project)}/imports`, {method:'POST', headers:{'X-CSRF-Token':csrf}, body:form})
      setJobId(result.jobs[0]); sessionStorage.setItem(`hmw.import.${project}`, result.jobs[0]); setPollCount(0); setTracking(true); setMessage('导入任务已提交')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function loadModules() {
    if (!project) return
    setBusy(true); setMessage('')
    try {
      const data = await api<string[]>(`/api/projects/${encodeURIComponent(project)}/modules`)
      setModules(data); setMessage('已有模块已加载，未列出的模块可手动输入。')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  function submitText() {
    const name = title.trim()
    if (!name || !text.trim()) return
    if (name === '.' || name === '..' || /[\\/\x00-\x1f]/.test(name)) {
      setMessage('知识标题不能包含路径或控制字符。')
      return
    }
    const filename = /\.(txt|md)$/i.test(name) ? name : `${name}.txt`
    void submit(new File([text], filename, {type:'text/plain;charset=utf-8'}))
  }

  async function loadHistory() {
    if (!project) return
    setBusy(true); setMessage(''); setHistory([])
    try {
      const result = await api<{jobs:JobSummary[]}>(`/api/projects/${encodeURIComponent(project)}/imports`)
      setHistory(result.jobs)
      if (result.jobs.length === 0) setMessage('暂无导入记录')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function refresh(selectedJobId: string = jobId) {
    if (!selectedJobId) return
    setBusy(true); setMessage(''); setDraft(null)
    try {
      const job = await api<Job>(`/api/imports/${encodeURIComponent(selectedJobId)}`)
      if (job.state === 'completed' && job.result?.knowledge_id) {
        setTracking(false)
        const version = await api<Draft>(`/api/knowledge/${encodeURIComponent(job.result.knowledge_id)}`)
        setDraft(version); setGuestVisible(version.guest_visible ?? false)
      } else if (job.state === 'needs_input') {
        setTracking(false)
        setMessage(inputMessages[job.result?.reason ?? ''] ?? '文件需要处理，请检查后重新上传。')
      } else if (job.state === 'failed') {
        setTracking(false)
        setMessage(`导入失败（${job.result?.reason ?? '未知原因'}）`)
      } else if (job.state === 'queued') {
        setMessage('任务等待 Worker 领取；如果长时间等待，请确认 Worker 已启动并使用相同的数据库和文件存储。页面自动刷新约一分钟，之后可手动刷新。')
      } else {
        setMessage('Worker 正在处理文件，完成后可预览草稿。')
      }
    } catch (error) { setTracking(false); setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function publish() {
    if (!draft) return
    setBusy(true); setMessage('')
    try {
      await api('/api/knowledge/publish-batch', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({version_ids:[draft.id], guest_visible:guestVisible})})
      setDraft({...draft, state:'published', guest_visible:guestVisible}); setMessage('知识已发布')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function saveVisibility() {
    if (!draft) return
    setBusy(true); setMessage('')
    try {
      await api(`/api/knowledge/${encodeURIComponent(draft.id)}/guest-visibility`, {method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({visible:guestVisible})})
      setDraft({...draft, guest_visible:guestVisible}); setMessage('访客可见范围已更新')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  return <section className="card knowledge-import"><h2>导入项目知识</h2><p>支持 UTF-8 文本、Word 和 PowerPoint，先预览草稿再发布。</p>
    <label>导入项目<select value={project} disabled={busy} onChange={event=>{setProject(event.target.value); setModule('公共知识'); setModules(['公共知识']); setTracking(Boolean(sessionStorage.getItem(`hmw.import.${event.target.value}`))); setPollCount(0); setJobId(sessionStorage.getItem(`hmw.import.${event.target.value}`) ?? ''); setDraft(null); setHistory([]); setMessage('')}}><option value="">选择项目</option>{projects.filter(item=>item.role === 'owner' || item.role === 'maintainer').map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>特性模块<input list="knowledge-modules" value={module} maxLength={200} disabled={busy} onChange={event=>setModule(event.target.value)} /></label><datalist id="knowledge-modules">{modules.map(item=><option key={item} value={item} />)}</datalist>
    <p>默认“公共知识”，也可输入新模块；问题的功能模块名称与此一致时会优先匹配对应 Skill。</p><button type="button" className="secondary" disabled={busy || !project} onClick={()=>void loadModules()}>加载已有模块</button>
    <label>选择知识文件<input type="file" disabled={busy} accept=".md,.txt,.doc,.docx,.ppt,.pptx" onChange={event=>{setFile(event.target.files?.[0] ?? null); setJobId(''); setDraft(null); setMessage('')}} /></label>
    <div className="actions"><button type="button" onClick={()=>submit(file)} disabled={busy || !project || !file || !module.trim()}>提交导入</button><button type="button" className="secondary" onClick={()=>refresh()} disabled={busy || !jobId}>刷新任务</button></div>
    <details className="manual-import"><summary>手动输入文字</summary><p>输入标题和内容，提交后将生成可预览、可发布的知识草稿。</p><label>知识标题<input value={title} maxLength={240} disabled={busy} onChange={event=>setTitle(event.target.value)} placeholder="例如：接口说明" /></label><label>知识内容<textarea value={text} disabled={busy} rows={6} onChange={event=>setText(event.target.value)} placeholder="在这里输入知识内容" /></label><button type="button" onClick={submitText} disabled={busy || !project || !module.trim() || !title.trim() || !text.trim()}>导入文字</button></details>
    <div className="actions"><button type="button" className="secondary" onClick={loadHistory} disabled={busy || !project}>加载导入记录</button></div>
    {history.length > 0 && <ul>{history.map(item=><li key={item.id}>{item.filename} · {jobLabels[item.state] ?? '未知状态'} · {new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', dateStyle:'short', timeStyle:'short'}).format(new Date(item.created_at))} <button type="button" disabled={busy} onClick={()=>{setJobId(item.id); setPollCount(0); setTracking(true); refresh(item.id)}}>查看任务</button></li>)}</ul>}
    {message && <p role="status">{message}</p>}
    {draft && <div><h3>来源：{draft.source.filename}</h3><p>特性模块：{draft.module ?? '公共知识'}</p><a href={`/api/sources/${encodeURIComponent(draft.source.id)}/download`}>下载原文件</a><p>{draft.locator.kind === 'docx' ? 'Word 段落 / 表格定位' : draft.locator.kind === 'pptx' ? 'PowerPoint 幻灯片 / 元素定位' : `行 ${draft.locator.start_line}–${draft.locator.end_line}`}</p><p>{draft.state === 'published' ? '已发布' : draft.state === 'retired' ? '已停用' : '草稿'}</p>{draft.locator.warnings?.map(warning=><p key={warning}>{reviewWarnings[warning] ?? '原文件存在需要人工核对。'}</p>)}{draft.locator.anchors && <details><summary>查看来源定位</summary><ul>{draft.locator.anchors.map((anchor,index)=><li key={index}>第 {anchor.line} 行：{draft.locator.kind === 'docx' ? `${anchor.kind === 'table' ? '表格' : '段落'} ${anchor.index}` : `幻灯片 ${anchor.slide}，元素 ${anchor.element}`}</li>)}</ul></details>}<pre>{draft.content}</pre>{draft.state !== 'retired' && <label className="check"><input type="checkbox" checked={guestVisible} disabled={busy} onChange={event=>setGuestVisible(event.target.checked)} />允许临时访客查看此知识</label>}{draft.state === 'published' && <button type="button" className="secondary" disabled={busy || guestVisible === (draft.guest_visible ?? false)} onClick={saveVisibility}>保存访客可见范围</button>}<button type="button" onClick={publish} disabled={busy || draft.state !== 'draft'}>发布知识</button></div>}
    <SkillManager project={project} module={module} csrf={csrf} />
  </section>
}
