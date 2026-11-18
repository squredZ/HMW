import NavigationIcon from './NavigationIcon'
import { useEffect, useState } from 'react'
import GuestQuestions from './GuestQuestions'
import GuestIssues from './GuestIssues'

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
  const [section,setSection] = useState<'questions' | 'knowledge' | 'status' | 'issues'>('questions')
  const [sessionReady,setSessionReady] = useState(false)
  const [status,setStatus] = useState<GuestStatus | null>(null)
  const [projects,setProjects] = useState<Project[]>([])
  const [questionProjects,setQuestionProjects] = useState<Project[]>([])
  const [questionLoading,setQuestionLoading] = useState(true)
  const [questionLoaded,setQuestionLoaded] = useState(false)
  const [questionError,setQuestionError] = useState('')
  const [projectsLoading,setProjectsLoading] = useState(true)
  const [projectError,setProjectError] = useState('')
  const [entries,setEntries] = useState<Entry[]>([])
  const [knowledge,setKnowledge] = useState<Knowledge | null>(null)
  const [message,setMessage] = useState('')
  const [busy,setBusy] = useState(false)

  async function loadProjects(signal?: AbortSignal) {
    setProjectsLoading(true); setProjectError('')
    try {
      const result = await getJson<Project[]>('/api/guest/projects', {signal})
      if (!signal?.aborted) { setProjects(result); setEntries([]); setKnowledge(null) }
    } catch (error) {
      if (!signal?.aborted) { setProjectError((error as Error).message); setProjects([]); setEntries([]); setKnowledge(null) }
    } finally {
      if (!signal?.aborted) setProjectsLoading(false)
    }
  }

  async function loadQuestionProjects(signal?: AbortSignal) {
    setQuestionLoading(true); setQuestionError('')
    try {
      const result = await getJson<Project[]>('/api/guest/question-projects', {signal})
      if (!signal?.aborted) { setQuestionProjects(result); setQuestionLoaded(true) }
    } catch (error) {
      if (!signal?.aborted) { setQuestionError((error as Error).message); setQuestionProjects([]) }
    } finally {
      if (!signal?.aborted) setQuestionLoading(false)
    }
  }

  useEffect(()=>{
    const controller = new AbortController()
    async function start() {
      try {
        await getJson('/api/guest/session', {method:'POST', signal:controller.signal})
      } catch (error) {
        if (!controller.signal.aborted) { setProjectError((error as Error).message); setProjectsLoading(false); setQuestionError((error as Error).message); setQuestionLoading(false) }
        return
      }
      if (!controller.signal.aborted) setSessionReady(true)
      const statusRequest = (async () => {
        try {
          const response = await fetch('/api/guest/status', {credentials:'same-origin',signal:controller.signal})
          if (response.ok) setStatus(await response.json())
          else if (response.status !== 404) setMessage('无法读取当前工作状态。')
        } catch {
          if (!controller.signal.aborted) setMessage('无法读取当前工作状态。')
        }
      })()
      await Promise.all([statusRequest, loadProjects(controller.signal), loadQuestionProjects(controller.signal)])
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

  return <div className="shell"><aside className="sidebar"><div className="brand"><span className="mark">W</span><div>HMW <small>WORKBENCH</small></div></div><p className="caption">探索工作台</p><nav className="side-nav" aria-label="访客导航"><button type="button" className={section === 'questions' ? 'nav-item active' : 'nav-item'} aria-current={section === 'questions' ? 'page' : undefined} onClick={()=>setSection('questions')}><NavigationIcon name="ask" />问一问</button><button type="button" className={section === 'knowledge' ? 'nav-item active' : 'nav-item'} aria-current={section === 'knowledge' ? 'page' : undefined} onClick={()=>setSection('knowledge')}><NavigationIcon name="knowledge" />项目知识</button><button type="button" className={section === 'status' ? 'nav-item active' : 'nav-item'} aria-current={section === 'status' ? 'page' : undefined} onClick={()=>setSection('status')}><NavigationIcon name="person" />关于我</button><button type="button" className={section === 'issues' ? 'nav-item active' : 'nav-item'} aria-current={section === 'issues' ? 'page' : undefined} onClick={()=>setSection('issues')}><NavigationIcon name="import" />登记问题</button></nav><div className="sidebar-bottom"><span className="avatar guest-avatar">访</span><div><strong>访客空间</strong><small>临时访问会话</small></div></div></aside><main>
    <header className="topbar"><div><span className="eyebrow">HMW / {section === 'questions' ? '问一问' : section === 'knowledge' ? '项目知识' : section === 'issues' ? '登记问题' : '关于我'}</span><h1>{section === 'questions' ? '有什么可以帮你查找？' : section === 'knowledge' ? '项目知识' : section === 'issues' ? '登记问题' : '关于我'}</h1><p>{section === 'questions' ? '从分享的资料中寻找有出处的信息。' : section === 'knowledge' ? '浏览负责人分享的项目资料。' : section === 'issues' ? '把遇到的问题记录给负责人。' : '了解负责人分享的工作状态。'}</p></div><button type="button" className="secondary" onClick={onOwnerLogin}>负责人登录</button></header>
    <div hidden={section !== 'issues'}>{section === 'issues' && sessionReady && <GuestIssues projects={questionProjects} />}{questionError && <p role="status">项目列表加载失败：{questionError}</p>}<button type="button" className="secondary" disabled={questionLoading} onClick={()=>loadQuestionProjects()}>刷新可用项目</button></div>
    <div hidden={section !== 'status'}>    <section className="card"><h2>关于我 · 当前工作状态</h2>{status ? <><p>{status.focus}</p><p>进度：{status.progress === null ? '未填写' : `${status.progress}%`}</p><p>截至手动更新：{new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', dateStyle:'short', timeStyle:'short'}).format(new Date(status.updated_at))}（北京时间）</p>{status.allocations?.length > 0 && <><h3>每周投入</h3><ul>{status.allocations.map((item,index)=><li key={index}>{item.project_name} · {item.week} · {item.unit === 'hours' ? '小时' : '%'} · 计划 {item.planned ?? '未填'} / 实际 {item.actual ?? '未填'}</li>)}</ul></>}</> : <p>暂未发布工作状态。</p>}</section></div>
    <div hidden={section !== 'questions'}><GuestQuestions projects={questionProjects} />
      {questionLoading && <p role="status">正在加载项目列表…</p>}
      {!questionLoading && !questionError && questionLoaded && questionProjects.length === 0 && <p>暂无可选择的项目，请联系负责人创建项目。</p>}
      {questionError && <p role="status">项目列表加载失败：{questionError}</p>}
      <button type="button" className="secondary" disabled={questionLoading} onClick={()=>loadQuestionProjects()}>刷新项目列表</button></div><div hidden={section !== 'knowledge'}><section className="card knowledge-import"><h2>项目知识</h2>{projects.length === 0 && <p>暂无分享的项目知识。</p>}<div className="actions">{projects.map(project=><button type="button" className="secondary" disabled={busy} key={project.id} onClick={()=>openProject(project)}>{project.name}</button>)}</div>
      {entries.length > 0 && <ul>{entries.map(entry=><li key={entry.id}><button type="button" className="secondary" disabled={busy} onClick={()=>openEntry(entry)}>{entry.filename}</button></li>)}</ul>}
      {knowledge && <div><h3>来源：{knowledge.source.filename}</h3><a href={`/api/guest/sources/${encodeURIComponent(knowledge.source.id)}/download`}>下载原文件</a><p>{knowledge.locator.kind === 'docx' ? '来源：Word 段落与表格' : knowledge.locator.kind === 'pptx' ? '来源：PowerPoint 幻灯片与元素' : `来源行 ${knowledge.locator.start_line}–${knowledge.locator.end_line}`}</p><pre>{knowledge.content}</pre></div>}
    </section></div>{message && <p role="status" className="notice">{message}</p>}
  </main></div>
}
