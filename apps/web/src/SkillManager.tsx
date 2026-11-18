import { useEffect, useRef, useState } from 'react'

type Skill = {id:string; name:string; module:string; content:string}

export default function SkillManager({project, module, csrf}:{project:string; module:string; csrf:string}) {
  const [name,setName] = useState('')
  const [content,setContent] = useState('')
  const [items,setItems] = useState<Skill[]>([])
  const [offset,setOffset] = useState(0)
  const [more,setMore] = useState(false)
  const [message,setMessage] = useState('')
  const [busy,setBusy] = useState(false)
  const generation = useRef(0)
  const request = useRef<AbortController | null>(null)

  useEffect(() => { generation.current++; request.current?.abort(); setBusy(false); setItems([]); setOffset(0); setMore(false); setName(''); setContent(''); setMessage('') }, [project])
  useEffect(() => () => { generation.current++; request.current?.abort() }, [])

  async function load(next = 0) {
    if (!project) return
    const current = generation.current
    const controller = new AbortController(); request.current = controller
    setBusy(true); setMessage('')
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(project)}/skills?limit=50&offset=${next}`, {credentials:'same-origin', signal:controller.signal})
      if (!response.ok) throw new Error()
      const data: {items:Skill[]; has_more:boolean} = await response.json()
      if (current === generation.current) { setItems(data.items); setMore(data.has_more); setOffset(next); if (!data.items.length) setMessage('暂无 Skill') }
    } catch { if (current === generation.current) setMessage('Skill 加载失败，请重试。') }
    finally { if (current === generation.current) setBusy(false) }
  }

  async function save() {
    if (!project || !module.trim() || !name.trim() || !content.trim() || busy) return
    const current = generation.current
    const controller = new AbortController(); request.current = controller
    setBusy(true); setMessage('')
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(project)}/skills`, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({module:module.trim(),name:name.trim(),content:content.trim()}), signal:controller.signal})
      if (!response.ok) throw new Error()
      if (current === generation.current) { setName(''); setContent(''); setItems([]); setOffset(0); setMore(false); setMessage('Skill 已保存') }
    } catch { if (current === generation.current) setMessage('Skill 保存失败，请检查内容并重试。') }
    finally { if (current === generation.current) setBusy(false) }
  }

  async function retire(id:string) {
    const current = generation.current
    const controller = new AbortController(); request.current = controller
    setBusy(true); setMessage('')
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(project)}/skills/${encodeURIComponent(id)}`, {method:'DELETE', credentials:'same-origin',headers:{'X-CSRF-Token':csrf}, signal:controller.signal})
      if (!response.ok) throw new Error()
      if (current === generation.current) { setItems(existing=>existing.filter(item=>item.id !== id)); setMessage('Skill 已停用') }
    } catch { if (current === generation.current) setMessage('Skill 停用失败，请重试。') }
    finally { if (current === generation.current) setBusy(false) }
  }

  async function readFile(file:File | undefined) {
    if (!file) return
    if (!/\.(md|txt)$/i.test(file.name) || file.size > 24000) { setMessage('请选择不超过 24 KB 的 UTF-8 Markdown 或文本文件。'); return }
    const current = generation.current
    try {
      const text = new TextDecoder('utf-8', {fatal:true}).decode(await file.arrayBuffer())
      if (text.length > 8000) { if (current === generation.current) setMessage('Skill 内容不能超过 8000 个字符。'); return }
      if (current === generation.current) { setContent(text); setName(file.name.replace(/\.(md|txt)$/i, '')); setMessage('已载入文件，请检查后保存。') }
    } catch { if (current === generation.current) setMessage('文件无法按 UTF-8 读取。') }
  }

  return <section className="skill-manager"><h3>录入与管理 Skill</h3><p>保存到当前项目和上方选择的特性模块。分析时会匹配该模块与公共知识的 Skill。</p>
    <label>Skill 名称<input value={name} maxLength={200} disabled={busy} onChange={event=>setName(event.target.value)} /></label>
    <label>导入 Skill 文件<input type="file" accept=".md,.txt" disabled={busy} onChange={event=>void readFile(event.target.files?.[0])} /></label>
    <label>Skill 内容<textarea value={content} maxLength={8000} rows={8} disabled={busy} onChange={event=>setContent(event.target.value)} placeholder="填写排查步骤和判断方法" /></label>
    <div className="actions"><button type="button" disabled={busy || !project || !module.trim() || !name.trim() || !content.trim()} onClick={()=>void save()}>保存 Skill</button><button type="button" className="secondary" disabled={busy || !project} onClick={()=>void load()}>加载 Skill</button></div>
    {items.map(item=><details key={item.id}><summary>{item.name} · {item.module}</summary><pre>{item.content}</pre><button type="button" className="secondary" disabled={busy} onClick={()=>void retire(item.id)}>停用 {item.name}</button></details>)}
    {(offset > 0 || more) && <div className="actions"><button type="button" className="secondary" disabled={busy || offset === 0} onClick={()=>void load(Math.max(0,offset-50))}>Skill 上一页</button><button type="button" className="secondary" disabled={busy || !more} onClick={()=>void load(offset+50)}>Skill 下一页</button></div>}
    {message && <p role="status">{message}</p>}
  </section>
}
