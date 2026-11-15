import { FormEvent, useState } from 'react'
import './style.css'

type Allocation = {project_id: string; week: string; unit: 'hours' | 'percent'; planned: number | null; actual: number | null}
type Status = { id: string; version: number; state: string; focus: string; progress: number | null; updated_at?: string; audience_project_ids?: string[]; allocations?: Allocation[] }
async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {credentials: 'same-origin', ...options})
  if (!response.ok) throw new Error(response.status === 409 ? '内容已发生变化，请重新登录后再编辑。' : `请求失败（${response.status}）`)
  return response.status === 204 ? undefined as T : response.json()
}
export default function App() {
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [csrf, setCsrf] = useState('')
  const [status, setStatus] = useState<Status | null>(null)
  const [focus, setFocus] = useState(''), [progress, setProgress] = useState(''), [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [projects, setProjects] = useState<{id:string; name:string; role:string}[]>([])
  const [audience, setAudience] = useState<string[]>([])
  const [allocations, setAllocations] = useState<Allocation[]>([])
  const [project, setProject] = useState(''), [week, setWeek] = useState(''), [planned, setPlanned] = useState(''), [actual, setActual] = useState('')
  const [colleagueName, setColleagueName] = useState('')
  const [colleagueStatus, setColleagueStatus] = useState<Status | null>(null)
  const [projectName, setProjectName] = useState('')
  const [memberProject, setMemberProject] = useState(''), [memberName, setMemberName] = useState('')
  const [unit, setUnit] = useState<'hours' | 'percent'>('hours')
  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage('')
    try {
      const session = await request<{csrf_token: string}>('/api/dev/session', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username, password})})
      const current = await request<Status | null>('/api/personal/status')
      const available = await request<{id:string; name:string; role:string}[]>('/api/projects')
      setProjects(available); setAudience(current?.audience_project_ids ?? []); setAllocations(current?.allocations ?? [])
      setStatus(current); setFocus(current?.focus ?? ''); setProgress(current?.progress?.toString() ?? ''); setCsrf(session.csrf_token); setPassword('')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage('')
    try {
      const saved = await request<Status>('/api/personal/status', {method:'PUT', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify({focus, progress:progress === '' ? null : Number(progress), expected_version:status?.version ?? null, audience_project_ids:audience, allocations})})
      setStatus(saved); setMessage('草稿已保存')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }
  async function viewColleague() {
    if (!colleagueName.trim()) return
    setBusy(true); setMessage(''); setColleagueStatus(null)
    try { setColleagueStatus(await request<Status>(`/api/personal/status/${encodeURIComponent(colleagueName.trim())}`)) }
    catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }
  async function createProject() {
    if (!projectName.trim()) return
    setBusy(true); setMessage('')
    try {
      const created = await request<{id:string; name:string; role:string}>('/api/projects', {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify({name:projectName})})
      setProjects([...projects, created]); setProjectName('')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }
  async function addMember() {
    if (!memberProject || !memberName.trim()) return
    setBusy(true); setMessage('')
    try {
      await request(`/api/projects/${encodeURIComponent(memberProject)}/members/${encodeURIComponent(memberName.trim())}`, {method:'PUT', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify({role:'member'})})
      setMemberName(''); setMessage('成员已添加')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }
  function addAllocation() {
    if (!project || !week || (planned === '' && actual === '')) return
    setAllocations([...allocations, {project_id:project, week, unit, planned:planned === '' ? null : Number(planned), actual:actual === '' ? null : Number(actual)}])
    setPlanned(''); setActual('')
  }
  async function withdraw() {
    if (!status) return
    setBusy(true); setMessage('')
    try {
      const result = await request<Status>(`/api/personal/status/${status.id}/withdraw`, {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify({expected_version:status.version})})
      setStatus(result); setMessage('已撤回')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }
  async function publish() {
    if (!status) return
    setBusy(true); setMessage('')
    try {
      const published = await request<Status>(`/api/personal/status/${status.id}/publish`, {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify({expected_version:status.version})})
      setStatus(published); setMessage('已发布')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }
  return <div className="shell">
    <aside><div className="brand"><span className="mark">W</span><div>HMW <small>WORKBENCH</small></div></div><p className="caption">个人工作空间</p><div className="active">◉ <span>关于我</span></div><p className="aside-note">本地开发预览<br />更多工作台功能正在建设中</p></aside>
    <main><header><div><span className="eyebrow">PERSONAL WORKSPACE</span><h1>关于我 <span>·</span> 当前工作状态</h1><p>由本人手动维护的工作重点与进度。</p></div><span className="pill">本地开发</span></header>
      {!csrf ? <section className="card login"><div className="section-title"><span>01</span><div><h2>登录工作台</h2><p>使用本地开发账号继续。</p></div></div><form onSubmit={login}><label>账号<input value={username} onChange={e=>setUsername(e.target.value)} required autoComplete="username" /></label><label>密码<input type="password" value={password} onChange={e=>setPassword(e.target.value)} required autoComplete="current-password" /></label><button disabled={busy}>登录</button></form></section> : <section className="card"><div className="section-title"><span>01</span><div><h2>手动更新状态</h2><p>先保存草稿，确认后再发布。</p></div></div><form onSubmit={save}><label>当前重点<textarea value={focus} onChange={e=>setFocus(e.target.value)} rows={5} placeholder="记录你目前关注的工作" /></label><label>进度（%）<input type="number" min="0" max="100" value={progress} onChange={e=>setProgress(e.target.value)} placeholder="可选" /></label><fieldset><legend>项目</legend><label>项目名称<input value={projectName} onChange={e=>setProjectName(e.target.value)} /></label><button type="button" className="secondary" disabled={busy || !projectName.trim()} onClick={createProject}>创建项目</button><label>成员所属项目<select value={memberProject} onChange={e=>setMemberProject(e.target.value)}><option value="">选择本人创建的项目</option>{projects.filter(p=>p.role === 'owner').map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>成员账号<input value={memberName} onChange={e=>setMemberName(e.target.value)} /></label><button type="button" className="secondary" disabled={busy || !memberProject || !memberName.trim()} onClick={addMember}>添加成员</button></fieldset><fieldset><legend>发布范围</legend>{projects.length === 0 && <p>暂无可选项目，默认仅本人可见。</p>}{projects.map(p => <label className="check" key={p.id}><input type="checkbox" checked={audience.includes(p.id)} onChange={e=>setAudience(e.target.checked ? [...audience,p.id] : audience.filter(id=>id!==p.id))} />向{p.name}成员发布</label>)}</fieldset><fieldset><legend>每周投入</legend><label>项目<select value={project} onChange={e=>setProject(e.target.value)}><option value="">选择项目</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>周起始日期<input type="date" value={week} onChange={e=>setWeek(e.target.value)} /></label><label>单位<select value={unit} onChange={e=>setUnit(e.target.value as 'hours' | 'percent')}><option value="hours">小时</option><option value="percent">百分比</option></select></label><label>计划投入<input type="number" min="0" max={unit === 'percent' ? 100 : undefined} value={planned} onChange={e=>setPlanned(e.target.value)} /></label><label>实际投入<input type="number" min="0" max={unit === 'percent' ? 100 : undefined} value={actual} onChange={e=>setActual(e.target.value)} /></label><button type="button" className="secondary" onClick={addAllocation}>添加投入</button><ul>{allocations.map((a,i)=><li key={i}>{projects.find(p=>p.id===a.project_id)?.name ?? '项目不可见'} · {a.week} · {a.unit === 'hours' ? '小时' : '%'} · 计划 {a.planned ?? '未填'} / 实际 {a.actual ?? '未填'} <button type="button" onClick={()=>setAllocations(allocations.filter((_,n)=>n!==i))}>删除</button></li>)}</ul></fieldset><div className="actions"><button disabled={busy}>保存草稿</button><button type="button" className="secondary" disabled={busy || !status || status.state !== 'draft' || focus !== status.focus || progress !== (status.progress?.toString() ?? '') || JSON.stringify(audience) !== JSON.stringify(status.audience_project_ids ?? []) || JSON.stringify(allocations) !== JSON.stringify(status.allocations ?? [])} onClick={publish}>发布</button>{status && <button type="button" className="secondary" disabled={busy || status.state === 'withdrawn'} onClick={withdraw}>撤回已发布状态</button>}{status && <span className="version">版本 {status.version} · {status.state === 'published' ? '已发布' : status.state === 'withdrawn' ? '已撤回' : '草稿'}</span>}</div></form></section>}
      {csrf && <section className="card"><h2>查看同事状态</h2><label>查看同事账号<input value={colleagueName} onChange={e=>{setColleagueName(e.target.value); setColleagueStatus(null)}} /></label><button type="button" onClick={viewColleague} disabled={busy || !colleagueName.trim()}>查看已发布状态</button>{colleagueStatus && <div><p>截至手动更新：{colleagueStatus.updated_at}</p><p>{colleagueStatus.focus}</p><p>进度：{colleagueStatus.progress === null ? '未填写' : `${colleagueStatus.progress}%`}</p><ul>{colleagueStatus.allocations?.map((a,i)=><li key={i}>{projects.find(p=>p.id===a.project_id)?.name ?? '项目'} · {a.week} · {a.unit === 'hours' ? '小时' : '%'} · 计划 {a.planned ?? '未填'} / 实际 {a.actual ?? '未填'}</li>)}</ul></div>}</section>}{message && <p role="status" className="notice">{message}</p>}<footer>内容仅供本地开发验证，请勿录入真实敏感数据。</footer>
    </main>
  </div>
}
