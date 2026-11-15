import { FormEvent, useEffect, useState } from 'react'

type Project = {id:string; name:string}
type Locator = {kind?:string; start_line?:number; end_line?:number; index?:number; block_kind?:string; slide?:number; element?:number}
type Evidence = {knowledge_id:string; source_id:string; filename:string; locator:Locator; excerpt:string; guest_visible?:boolean}
type Answer = {kind:'extractive' | 'insufficient_evidence' | 'generated'; message:string; search_limited:boolean; unavailable_sources?:number; agent_unavailable?:boolean; agent_invoked?:boolean; retrieval_mode?:'keyword' | 'context'; citations?:number[]; evidence:Evidence[]}
type Turn = {question:string; answer:Answer}

function conversationId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`
}

function location(locator:Locator):string {
  if (locator.kind === 'docx') return `${locator.block_kind === 'table' ? '表格' : '段落'} ${locator.index}`
  if (locator.kind === 'pptx') return `幻灯片 ${locator.slide} · 元素 ${locator.element}`
  return `第 ${locator.start_line} 行`
}

export default function GuestQuestions({projects}:{projects:Project[]}) {
  const [project,setProject] = useState('')
  const [question,setQuestion] = useState('')
  const [turns,setTurns] = useState<Turn[]>([])
  const [conversation,setConversation] = useState(conversationId)
  const [error,setError] = useState('')
  const [busy,setBusy] = useState(false)

  function resetConversation() {
    setTurns([]); setQuestion(''); setError(''); setConversation(conversationId())
  }

  useEffect(() => {
    if (project && !projects.some(item => item.id === project)) {
      setProject(''); resetConversation()
    }
  }, [project, projects])

  async function ask(event:FormEvent) {
    event.preventDefault()
    if (!project || question.trim().length < 2) return
    const submitted = question.trim()
    setBusy(true); setError('')
    try {
      const response = await fetch('/api/guest/questions', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:project,question:submitted,conversation_id:conversation})})
      if (!response.ok) throw new Error(response.status === 401 ? '临时会话已失效，请刷新页面。' : response.status === 409 ? '对话正在处理或资料已变化，请稍后重试。' : '无法访问该项目的问答，请确认模型配置或项目状态。')
      const answer: Answer = await response.json()
      setTurns(current => [...current, {question:submitted, answer}].slice(-50)); setQuestion('')
    } catch (caught) { setError((caught as Error).message) } finally { setBusy(false) }
  }

  return <section className="card question-panel"><h2>问一问</h2><p>针对所选项目连续提问。启用 Agent 后可使用该项目全部已发布知识，回答可能包含未开放下载的内容；请核对来源。</p>
    <label>问答项目<select value={project} disabled={busy} onChange={event=>{setProject(event.target.value);resetConversation()}}><option value="">选择项目</option>{projects.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <div className="conversation-toolbar"><button type="button" className="secondary" disabled={busy || !turns.length} onClick={resetConversation}>开始新对话</button><span>最近 5 轮用于追问，最多显示 50 轮；切换项目或刷新页面会开始新对话</span></div>
    <div className="conversation" aria-live="polite">{turns.map((turn, turnIndex)=><div key={turnIndex} className="conversation-turn"><div className="conversation-user"><span>你</span><p>{turn.question}</p></div><div className="conversation-assistant"><span>知识助手</span>
      {turn.answer.kind === 'generated' && <p>AI 生成，请核对来源。</p>}
      {turn.answer.kind === 'insufficient_evidence' && turn.answer.agent_invoked && !turn.answer.agent_unavailable && <p>Agent 已检查可用资料，未找到足够依据。</p>}
      {turn.answer.kind === 'insufficient_evidence' && !turn.answer.agent_invoked && <p>没有可用于分析的资料，本次未调用 Agent。</p>}
      {turn.answer.kind === 'extractive' && !turn.answer.agent_invoked && <p>当前仅展示检索原文；如需 AI 回答，请确认模型已启用。</p>}
      {turn.answer.kind === 'generated' && turn.answer.retrieval_mode === 'context' && <p>未命中关键词，已使用有限的项目资料上下文。</p>}
      {turn.answer.agent_unavailable && <p>模型暂不可用，以下为原文检索结果。</p>}<p>{turn.answer.message}</p>
      {turn.answer.search_limited && <p>本次仅检索了部分资料，请缩小范围或补充关键词。</p>}{(turn.answer.unavailable_sources ?? 0) > 0 && <p>部分来源暂时无法校验，已从结果中排除。</p>}
      {turn.answer.evidence.map((item,index)=><article key={`${item.knowledge_id}-${index}`}><p>{turn.answer.kind === 'generated' && `[${index + 1}] `}{item.filename} · {location(item.locator)}</p><blockquote>{item.excerpt}</blockquote>{item.guest_visible !== false ? <a href={`/api/guest/sources/${encodeURIComponent(item.source_id)}/download`}>下载来源</a> : <small>原件未对访客开放下载</small>}</article>)}
    </div></div>)}</div>
    <form onSubmit={ask}><label>问题<textarea value={question} onChange={event=>setQuestion(event.target.value)} maxLength={240} rows={3} placeholder="输入问题或继续追问" /></label><button disabled={busy || !project || question.trim().length < 2}>{busy ? '正在思考…' : '发送'}</button></form>
    {error && <p role="status">{error}</p>}
  </section>
}
