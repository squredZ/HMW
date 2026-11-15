import { FormEvent, useState } from 'react'
import './style.css'

type Status = { id: string; version: number; state: string; focus: string; progress: number | null; updated_at?: string }
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
  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage('')
    try {
      const session = await request<{csrf_token: string}>('/api/dev/session', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username, password})})
      const current = await request<Status | null>('/api/personal/status')
      setStatus(current); setFocus(current?.focus ?? ''); setProgress(current?.progress?.toString() ?? ''); setCsrf(session.csrf_token); setPassword('')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage('')
    try {
      const saved = await request<Status>('/api/personal/status', {method:'PUT', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify({focus, progress:progress === '' ? null : Number(progress), expected_version:status?.version ?? null})})
      setStatus(saved); setMessage('草稿已保存')
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
      {!csrf ? <section className="card login"><div className="section-title"><span>01</span><div><h2>登录工作台</h2><p>使用本地开发账号继续。</p></div></div><form onSubmit={login}><label>账号<input value={username} onChange={e=>setUsername(e.target.value)} required autoComplete="username" /></label><label>密码<input type="password" value={password} onChange={e=>setPassword(e.target.value)} required autoComplete="current-password" /></label><button disabled={busy}>登录</button></form></section> : <section className="card"><div className="section-title"><span>01</span><div><h2>手动更新状态</h2><p>先保存草稿，确认后再发布。</p></div></div><form onSubmit={save}><label>当前重点<textarea value={focus} onChange={e=>setFocus(e.target.value)} rows={5} placeholder="记录你目前关注的工作" /></label><label>进度（%）<input type="number" min="0" max="100" value={progress} onChange={e=>setProgress(e.target.value)} placeholder="可选" /></label><div className="actions"><button disabled={busy}>保存草稿</button><button type="button" className="secondary" disabled={busy || !status || status.state !== 'draft' || focus !== status.focus || progress !== (status.progress?.toString() ?? '')} onClick={publish}>发布</button>{status && <span className="version">版本 {status.version} · {status.state === 'published' ? '已发布' : '草稿'}</span>}</div></form></section>}
      {message && <p role="status" className="notice">{message}</p>}<footer>内容仅供本地开发验证，请勿录入真实敏感数据。</footer>
    </main>
  </div>
}
