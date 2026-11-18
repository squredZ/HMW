import { useState } from 'react'
import { issueActions, issueStateLabels } from './issueStates'

type Issue = { id: string; project_name: string; title: string; description: string; state: string; version: number }

export default function OwnerIssues({ csrf }: { csrf: string }) {
  const [items, setItems] = useState<Issue[]>([])
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [reasons, setReasons] = useState<Record<string, string>>({})

  async function load(next = 0) {
    setBusy(true); setMessage('')
    try {
      const response = await fetch(`/api/issues?limit=50&offset=${next}`, {credentials:'same-origin'})
      if (!response.ok) throw new Error(`加载失败（${response.status}）`)
      const result: {items:Issue[]; has_more:boolean} = await response.json()
      setItems(result.items); setReasons({}); setHasMore(result.has_more); setOffset(next); setLoaded(true)
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  async function transition(item: Issue, state: string) {
    const reason = reasons[item.id]?.trim()
    if (!reason) return
    setBusy(true); setMessage('')
    try {
      const response = await fetch(`/api/issues/${encodeURIComponent(item.id)}/transition`, {method:'POST', credentials:'same-origin',
        headers:{'X-CSRF-Token':csrf, 'Content-Type':'application/json'},
        body:JSON.stringify({state, reason, expected_version:item.version})})
      if (!response.ok) throw new Error(`操作失败（${response.status}），请重新加载。`)
      const result: {state:string; version:number} = await response.json()
      setItems(current => current.map(entry => entry.id === item.id ? {...entry, state:result.state, version:result.version} : entry))
      setReasons(current => ({...current, [item.id]:''}))
      setMessage('问题状态已更新。')
    } catch (error) { setMessage((error as Error).message) } finally { setBusy(false) }
  }

  return <section className="card"><h2>问题中心</h2><p>查看访客提交的问题，并手动标记处理结果。</p>
    <button type="button" disabled={busy} onClick={()=>load()}>加载问题</button>
    {loaded && items.length === 0 && <p>暂无问题。</p>}
    {items.map(item=><article className="issue-item" key={item.id}><p>{item.project_name} · {issueStateLabels[item.state] ?? '未知状态'}</p><h3>{item.title}</h3><p className="issue-description">{item.description}</p>
      {(issueActions[item.state] ?? []).length > 0 && <><label>处理说明<input value={reasons[item.id] ?? ''} maxLength={500} disabled={busy} onChange={event=>setReasons(current=>({...current,[item.id]:event.target.value}))} /></label>
        <div className="actions">{issueActions[item.state].map(action=><button key={action.state} type="button" className="secondary" disabled={busy || !reasons[item.id]?.trim()} onClick={()=>void transition(item, action.state)}>{action.label}</button>)}</div></>}
    </article>)}
    {(offset > 0 || hasMore) && <div className="actions"><button type="button" className="secondary" disabled={busy || offset === 0} onClick={()=>load(Math.max(0,offset-50))}>上一页</button><button type="button" className="secondary" disabled={busy || !hasMore || offset >= 10000} onClick={()=>load(offset+50)}>下一页</button></div>}
    {message && <p role="status">{message}</p>}
  </section>
}
