import { useEffect, useState } from 'react'

type GuestStatus = {focus:string; progress:number | null; updated_at:string; allocations:{project_name:string;week:string;unit:'hours'|'percent';planned:number | null;actual:number | null}[]}
type Project = {id:string; name:string}
type Entry = {id:string; filename:string}
type Knowledge = {id:string; content:string; source:{id:string; filename:string}; locator:{kind?:string; start_line?:number; end_line?:number}}

async function getJson<T>(url:string, options:RequestInit = {}): Promise<T> {
  const response = await fetch(url, {credentials:'same-origin', ...options})
  if (!response.ok) throw new Error(response.status === 401 ? '临时会话已失效，请刷新页面。' : `操作失败（${response.status}）`)
  return response.json()
}

export default function GuestPage({onOwnerLogin}:{onOwnerLogin:()=>void}) {
  const [status,setStatus] = useState<GuestStatus | null>(null)
  const [projects,setProjects] = useState<Project[]>([])
  const [entries,setEntries] = useState<Entry[]>([])
  const [knowledge,setKnowledge] = useState<Knowledge | null>(null)
  const [message,setMessage] = useState('')
  const [busy,setBusy] = useState(false)

  useEffect(()=>{
    const controller = new AbortController()
    async function start() {
      try {
        await getJson('/api/guest/session', {method:'POST', signal:controller.signal})
        const response = await fetch('/api/guest/status', {credentials:'same-origin',signal:controller.signal})
        if (response.ok) setStatus(await response.json())
        else if (response.status !== 404) throw new Error('无法读取当前工作状态。')
        setProjects(await getJson<Project[]>('/api/guest/projects',{signal:controller.signal}))
      } catch (error) {
        if (!controller.signal.aborted) setMessage((error as Error).message)
      }
    }
    void start()
    return ()=>controller.abort()
  },[])

  async function openProject(project:Project) {
    setBusy(true); setMessage(''); setEntries([]); setKnowledge(null)
    try { setEntries(await getJson<Entry[]>(`/api/guest/projects/${encodeURIComponent(project.id)}/knowledge`)) }
    catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }
  async function openEntry(entry:Entry) {
    setBusy(true); setMessage(''); setKnowledge(null)
    try { setKnowledge(await getJson<Knowledge>(`/api/guest/knowledge/${encodeURIComponent(entry.id)}`)) }
    catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  return <div className="shell"><aside><div className="brand"><span className="mark">W</span><div>HMW <small>WORKBENCH</small></div></div><p className="aside-note">临时访问会话</p></aside><main>
    <header><div><span className="eyebrow">WORKBENCH</span><h1>工作台</h1><p>查看负责人分享的工作状态和项目知识。</p></div><button type="button" className="secondary" onClick={onOwnerLogin}>负责人登录</button></header>
    <section className="card"><h2>关于我 · 当前工作状态</h2>{status ? <><p>{status.focus}</p><p>进度：{status.progress === null ? '未填写' : `${status.progress}%`}</p><p>截至手动更新：{new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', dateStyle:'short', timeStyle:'short'}).format(new Date(status.updated_at))}（北京时间）</p>{status.allocations?.length > 0 && <><h3>每周投入</h3><ul>{status.allocations.map((item,index)=><li key={index}>{item.project_name} · {item.week} · {item.unit === 'hours' ? '小时' : '%'} · 计划 {item.planned ?? '未填'} / 实际 {item.actual ?? '未填'}</li>)}</ul></>}</> : <p>暂未发布工作状态。</p>}</section>
    <section className="card knowledge-import"><h2>项目知识</h2>{projects.length === 0 && <p>暂无分享的项目知识。</p>}<div className="actions">{projects.map(project=><button type="button" className="secondary" disabled={busy} key={project.id} onClick={()=>openProject(project)}>{project.name}</button>)}</div>
      {entries.length > 0 && <ul>{entries.map(entry=><li key={entry.id}><button type="button" className="secondary" disabled={busy} onClick={()=>openEntry(entry)}>{entry.filename}</button></li>)}</ul>}
      {knowledge && <div><h3>来源：{knowledge.source.filename}</h3><a href={`/api/guest/sources/${encodeURIComponent(knowledge.source.id)}/download`}>下载原文件</a><p>{knowledge.locator.kind === 'docx' ? '来源：Word 段落与表格' : knowledge.locator.kind === 'pptx' ? '来源：PowerPoint 幻灯片与元素' : `来源行 ${knowledge.locator.start_line}–${knowledge.locator.end_line}`}</p><pre>{knowledge.content}</pre></div>}
    </section>{message && <p role="status" className="notice">{message}</p>}
  </main></div>
}
