import { FormEvent, useState } from 'react'

type Project = { id: string; name: string }

export default function GuestIssues({ projects }: { projects: Project[] }) {
  const [project, setProject] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!projects.some(item => item.id === project) || !title.trim() || !description.trim()) return
    setBusy(true); setMessage('')
    try {
      const response = await fetch('/api/guest/issues', {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({project_id:project, title:title.trim(), description:description.trim()})})
      if (!response.ok) throw new Error(response.status === 401 ? '临时会话已失效，请刷新页面。' : '提交失败，请确认项目仍可访问后重试。')
      setTitle(''); setDescription(''); setMessage('问题已提交给负责人。')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  return <section className="card"><h2>登记问题</h2><p>选择已分享的项目，描述遇到的问题，供负责人查看处理。</p>
    {projects.length === 0 && <p>暂无可提交问题的项目，请联系负责人开放项目知识。</p>}
    <form onSubmit={submit}><label>问题所属项目<select value={projects.some(item => item.id === project) ? project : ''} disabled={busy} onChange={event=>setProject(event.target.value)}><option value="">选择项目</option>{projects.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>问题标题<input value={title} maxLength={200} disabled={busy} onChange={event=>setTitle(event.target.value)} /></label>
      <label>问题描述<textarea value={description} maxLength={10000} rows={6} disabled={busy} onChange={event=>setDescription(event.target.value)} placeholder="描述现象、预期结果和必要的复现步骤" /></label>
      <button disabled={busy || !projects.some(item => item.id === project) || !title.trim() || !description.trim()}>提交问题</button>
    </form>{message && <p role="status">{message}</p>}
  </section>
}
