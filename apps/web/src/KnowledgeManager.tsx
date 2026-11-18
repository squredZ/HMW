import { useState } from 'react'
import { reviewWarnings } from './KnowledgeImport'

type Project = { id: string; name: string; role: string }
type Item = { id: string; filename: string; state: string; module?:string; guest_visible: boolean }
type Detail = { id: string; state: string; module?:string; content: string; guest_visible: boolean; source: { id: string; filename: string }; locator?: { kind?: 'docx' | 'pptx'; start_line?: number; end_line?: number; warnings?: string[]; anchors?: { line: number; kind?: string; index?: number; slide?: number; element?: number }[] } }
const labels: Record<string, string> = { draft: '草稿', published: '已发布', retired: '已停用' }

async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin', ...options })
  if (!response.ok) throw new Error(`操作失败（${response.status}），请重新加载后重试。`)
  return response.json()
}

export default function KnowledgeManager({ csrf, projects }: { csrf: string; projects: Project[] }) {
  const [project, setProject] = useState('')
  const [state, setState] = useState('')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<Item[]>([])
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function load(nextOffset = 0) {
    if (!project) return
    setBusy(true); setMessage(''); setDetail(null)
    try {
      const params = new URLSearchParams({ limit: '50', offset: String(nextOffset) })
      if (state) params.set('state', state)
      if (query.trim()) params.set('q', query.trim())
      const data = await api<{ items: Item[]; has_more: boolean }>(`/api/projects/${encodeURIComponent(project)}/knowledge?${params}`)
      setItems(data.items); setHasMore(data.has_more); setOffset(nextOffset)
      if (!data.items.length) setMessage('没有符合条件的知识')
    } catch (error) { setItems([]); setHasMore(false); setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function open(id: string) {
    setBusy(true); setMessage(''); setDetail(null)
    try {
      const data = await api<Detail>(`/api/knowledge/${encodeURIComponent(id)}`)
      setDetail(data); setVisible(data.guest_visible)
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function change(action: 'publish' | 'visibility' | 'retire') {
    if (!detail) return
    setBusy(true); setMessage('')
    try {
      const headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }
      if (action === 'publish') {
        await api('/api/knowledge/publish-batch', { method: 'POST', headers, body: JSON.stringify({ version_ids: [detail.id], guest_visible: visible }) })
      } else if (action === 'visibility') {
        await api(`/api/knowledge/${encodeURIComponent(detail.id)}/guest-visibility`, { method: 'PUT', headers, body: JSON.stringify({ visible }) })
      } else {
        await api(`/api/knowledge/${encodeURIComponent(detail.id)}/retire`, { method: 'POST', headers })
      }
      const next = { ...detail, state: action === 'publish' ? 'published' : action === 'retire' ? 'retired' : detail.state, guest_visible: action === 'retire' ? false : visible }
      setDetail(next); setVisible(next.guest_visible)
      setItems(current => current.map(item => item.id === next.id ? { ...item, state: next.state, guest_visible: next.guest_visible } : item).filter(item => !state || item.state === state))
      setMessage(action === 'publish' ? '知识已发布' : action === 'retire' ? '知识已停用，访客无法继续查看。' : '访客可见范围已更新')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  function reset() { setItems([]); setDetail(null); setOffset(0); setHasMore(false); setMessage('') }
  return <section id="knowledge-management" className="card"><h2>知识管理</h2><p>按项目查看导入的知识，管理发布状态和访客访问范围。</p>
    <label>管理项目<select value={project} disabled={busy} onChange={event => { setProject(event.target.value); reset() }}><option value="">选择项目</option>{projects.filter(item => item.role === 'owner' || item.role === 'maintainer').map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>知识状态<select value={state} disabled={busy} onChange={event => { setState(event.target.value); reset() }}><option value="">全部</option><option value="draft">草稿</option><option value="published">已发布</option><option value="retired">已停用</option></select></label>
    <label>按文件名筛选<input value={query} maxLength={255} disabled={busy} onChange={event => { setQuery(event.target.value); reset() }} /></label>
    <button type="button" disabled={busy || !project} onClick={() => load()}>加载知识</button>
    <ul>{items.map(item => <li key={item.id}>{item.filename} · {item.module ?? '公共知识'} · {labels[item.state] ?? item.state} · {item.guest_visible ? '访客可见' : '访客不可见'} <button type="button" disabled={busy} onClick={() => open(item.id)}>查看 {item.filename}</button></li>)}</ul>
    {(offset > 0 || hasMore) && <div className="actions"><button type="button" className="secondary" disabled={busy || offset === 0} onClick={() => load(Math.max(0, offset - 50))}>上一页</button><button type="button" className="secondary" disabled={busy || !hasMore || offset >= 10000} onClick={() => load(offset + 50)}>下一页</button></div>}
    {message && <p role="status">{message}</p>}
    {detail && <div><h3>来源：{detail.source.filename}</h3><p>特性模块：{detail.module ?? '公共知识'}</p><p>{labels[detail.state] ?? detail.state}</p><a href={`/api/sources/${encodeURIComponent(detail.source.id)}/download`}>下载原文件</a>{detail.locator && <p>{detail.locator.kind === 'docx' ? 'Word 段落 / 表格定位' : detail.locator.kind === 'pptx' ? 'PowerPoint 幻灯片 / 元素定位' : `行 ${detail.locator.start_line}–${detail.locator.end_line}`}</p>}
      {detail.locator?.warnings?.map(warning => <p key={warning}>{reviewWarnings[warning] ?? '原文件存在需要人工核对的内容。'}</p>)}
      {detail.locator?.anchors && <details><summary>查看来源定位</summary><ul>{detail.locator.anchors.map((anchor, index) => <li key={index}>第 {anchor.line} 行：{detail.locator?.kind === 'docx' ? `${anchor.kind === 'table' ? '表格' : '段落'} ${anchor.index}` : `幻灯片 ${anchor.slide}，元素 ${anchor.element}`}</li>)}</ul></details>}
      <pre>{detail.content}</pre>
      {detail.state !== 'retired' && <label className="check"><input type="checkbox" checked={visible} disabled={busy} onChange={event => setVisible(event.target.checked)} />允许临时访客查看此知识</label>}
      {detail.state === 'draft' && <button type="button" disabled={busy} onClick={() => change('publish')}>发布知识</button>}
      {detail.state === 'published' && <p>停用后访客将无法查看；如需再次发布，请重新导入文件。</p>}
      {detail.state === 'published' && <div className="actions"><button type="button" className="secondary" disabled={busy || visible === detail.guest_visible} onClick={() => change('visibility')}>保存访客可见范围</button><button type="button" className="secondary" disabled={busy} onClick={() => change('retire')}>停用知识</button></div>}
    </div>}
  </section>
}
