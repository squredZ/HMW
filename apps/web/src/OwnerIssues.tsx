import { useState } from 'react'

type Issue = { id: string; project_name: string; title: string; description: string; state: string }

export default function OwnerIssues({ csrf }: { csrf: string }) {
  const [items, setItems] = useState<Issue[]>([])
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function load(next = 0) {
    setBusy(true); setMessage('')
    try {
      const response = await fetch(`/api/issues?limit=50&offset=${next}`, {credentials:'same-origin'})
      if (!response.ok) throw new Error(`加载失败（${response.status}）`)
      const result: {items:Issue[]; has_more:boolean} = await response.json()
      setItems(result.items); setHasMore(result.has_more); setOffset(next); setLoaded(true)
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function resolve(id: string) {
    setBusy(true); setMessage('')
    try {
      const response = await fetch(`/api/issues/${encodeURIComponent(id)}/resolve`, {method:'POST', credentials:'same-origin', headers:{'X-CSRF-Token':csrf}})
      if (!response.ok) throw new Error(`操作失败（${response.status}），请重新加载。`)
      setItems(current => current.map(item => item.id === id ? {...item, state:'resolved'} : item))
      setMessage('问题已标记为已解决。')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  return <section className="card"><h2>问题中心</h2><p>查看访客提交的问题，并手动标记处理结果。</p>
    <button type="button" disabled={busy} onClick={()=>load()}>加载问题</button>
    {loaded && items.length === 0 && <p>暂无问题。</p>}
    {items.map(item=><article className="issue-item" key={item.id}><p>{item.project_name} · {item.state === 'new' ? '待处理' : '已解决'}</p><h3>{item.title}</h3><p className="issue-description">{item.description}</p>{item.state === 'new' && <button type="button" className="secondary" disabled={busy} onClick={()=>resolve(item.id)}>标记已解决</button>}</article>)}
    {(offset > 0 || hasMore) && <div className="actions"><button type="button" className="secondary" disabled={busy || offset === 0} onClick={()=>load(Math.max(0,offset-50))}>上一页</button><button type="button" className="secondary" disabled={busy || !hasMore || offset >= 10000} onClick={()=>load(offset+50)}>下一页</button></div>}
    {message && <p role="status">{message}</p>}
  </section>
}
