import { FormEvent, useEffect, useRef, useState } from 'react'

type Project = {id:string; name:string}
type Locator = {kind?:string; start_line?:number; end_line?:number; index?:number; block_kind?:string; slide?:number; element?:number}
type Evidence = {knowledge_id:string; source_id:string; filename:string; locator:Locator; excerpt:string; guest_visible?:boolean}
type Answer = {kind:'extractive' | 'insufficient_evidence' | 'generated'; message:string; search_limited:boolean; unavailable_sources?:number; agent_unavailable?:boolean; agent_error?:'timeout' | 'busy' | 'context_limit' | 'unavailable'; agent_invoked?:boolean; ungrounded?:boolean; retrieval_mode?:'keyword' | 'context' | 'agent'; citations?:number[]; evidence:Evidence[]}
type Stage = 'thinking' | 'reading' | 'answering'
type Step = {stage:Stage; count:number}
type Turn = {question:string; answer:Answer; steps:Step[]}

function ProgressSteps({steps}:{steps:Step[]}) {
  const labels:Record<Stage,string> = {thinking:'思考问题', reading:'阅读资料', answering:'整理回答'}
  return <ol>{steps.map((step,index)=><li key={index}>{labels[step.stage]}{step.count > 1 && `（${step.count} 次）`}</li>)}</ol>
}

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
  return locator.end_line && locator.end_line !== locator.start_line ? `第 ${locator.start_line}–${locator.end_line} 行` : `第 ${locator.start_line} 行`
}

export default function GuestQuestions({projects}:{projects:Project[]}) {
  const [project,setProject] = useState('')
  const [question,setQuestion] = useState('')
  const [turns,setTurns] = useState<Turn[]>([])
  const [conversation,setConversation] = useState(conversationId)
  const [error,setError] = useState('')
  const [busy,setBusy] = useState(false)
  const [stage,setStage] = useState<Stage>('thinking')
  const [steps,setSteps] = useState<Step[]>([])
  const request = useRef<AbortController | null>(null)

  useEffect(() => () => { request.current?.abort(); request.current = null }, [])

  function resetConversation() {
    request.current?.abort(); request.current = null
    setBusy(false); setTurns([]); setQuestion(''); setError(''); setConversation(conversationId())
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
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setBusy(true); setError(''); setStage('thinking'); setSteps([])
    let progress:Step[] = []
    try {
      const response = await fetch('/api/guest/questions/stream', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:project,question:submitted,conversation_id:conversation}),signal:controller.signal})
      if (!response.ok) throw new Error(response.status === 401 ? '临时会话已失效，请刷新页面。' : response.status === 409 ? '对话正在处理或资料已变化，请稍后重试。' : '无法访问该项目的问答，请确认模型配置或项目状态。')
      if (!response.body) throw new Error('问答连接中断，请稍后重试。')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let answer: Answer | undefined
      try {
        while (true) {
          const {value, done} = await reader.read()
          if (controller.signal.aborted) return
          buffer += decoder.decode(value, {stream:!done})
          if (buffer.length > 1024 * 1024) throw new Error('问答处理失败，请稍后重试。')
          let boundary: RegExpExecArray | null
          while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
            const event = buffer.slice(0, boundary.index)
            buffer = buffer.slice(boundary.index + boundary[0].length)
            const data = event.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).replace(/^ /, '')).join('\n')
            if (!data) continue
            const message = JSON.parse(data)
            if (message.type === 'progress' && (message.stage === 'thinking' || message.stage === 'reading' || message.stage === 'answering')) {
              const nextStage:Stage = message.stage
              const last = progress[progress.length - 1]
              progress = last?.stage === nextStage
                ? [...progress.slice(0, -1), {stage:nextStage, count:last.count + 1}]
                : [...progress, {stage:nextStage, count:1}].slice(-30)
              setStage(nextStage); setSteps(progress)
            }
            else if (message.type === 'result') answer = message.data as Answer
            else if (message.type === 'error') throw new Error('问答处理失败，请稍后重试。')
          }
          if (done || answer) break
        }
      } finally {
        await reader.cancel().catch(() => {})
        reader.releaseLock()
      }
      if (!answer) throw new Error('问答连接中断，请稍后重试。')
      if (!controller.signal.aborted) {
        setTurns(current => [...current, {question:submitted, answer:answer!, steps:progress}].slice(-50)); setQuestion('')
      }
    } catch (caught) {
      if (!controller.signal.aborted) setError(caught instanceof Error && caught.message.startsWith('问答') ? caught.message : caught instanceof Error && (caught.message.startsWith('临时会话') || caught.message.startsWith('对话正在') || caught.message.startsWith('无法访问')) ? caught.message : '问答处理失败，请稍后重试。')
    } finally {
      if (request.current === controller) { request.current = null; setBusy(false) }
    }
  }

  return <section className="card question-panel"><h2>问一问</h2><p>针对所选项目连续提问。启用 Agent 后可使用该项目全部已发布知识，回答可能包含未开放下载的内容；请核对来源。</p>
    <label>问答项目<select value={project} disabled={busy} onChange={event=>{setProject(event.target.value);resetConversation()}}><option value="">选择项目</option>{projects.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <div className="conversation-toolbar"><button type="button" className="secondary" disabled={busy || !turns.length} onClick={resetConversation}>开始新对话</button><span>最近 5 轮用于追问，最多显示 50 轮；切换项目或刷新页面会开始新对话</span></div>
    <div className="conversation" aria-live="polite">{turns.map((turn, turnIndex)=><div key={turnIndex} className="conversation-turn"><div className="conversation-user"><span>你</span><p>{turn.question}</p></div><div className="conversation-assistant"><span>知识助手</span>{turn.steps.length > 0 && <details><summary>查看处理过程</summary><ProgressSteps steps={turn.steps} /></details>}
      {turn.answer.kind === 'generated' && <p>{turn.answer.ungrounded ? '该回答未引用项目知识，请勿作为项目事实依据。' : 'AI 生成，请核对来源。'}</p>}
      {turn.answer.kind === 'insufficient_evidence' && turn.answer.agent_invoked && !turn.answer.agent_unavailable && <p>Agent 已检查可用资料，未找到足够依据。</p>}
      {turn.answer.kind === 'insufficient_evidence' && !turn.answer.agent_invoked && <p>没有可用于分析的资料，本次未调用 Agent。</p>}
      {turn.answer.kind === 'extractive' && !turn.answer.agent_invoked && <p>当前仅展示检索原文；如需 AI 回答，请确认模型已启用。</p>}
      {turn.answer.kind === 'generated' && turn.answer.retrieval_mode === 'context' && <p>未命中关键词，已使用有限的项目资料上下文。</p>}
      {turn.answer.agent_unavailable && <p>{turn.answer.agent_error === 'timeout' ? '模型响应超时，请稍后重试。' : turn.answer.agent_error === 'busy' ? '问答请求较多，请稍后重试。' : turn.answer.agent_error === 'context_limit' ? '对话上下文超过限制，请开始新对话或缩小问题范围。' : '模型暂不可用，请稍后重试。'}{turn.answer.evidence.length > 0 && ' 以下为原文检索结果。'}</p>}<p>{turn.answer.message}</p>
      {turn.answer.search_limited && <p>本次仅检索了部分资料，请缩小范围或补充关键词。</p>}{(turn.answer.unavailable_sources ?? 0) > 0 && <p>部分来源暂时无法校验，已从结果中排除。</p>}
      {turn.answer.evidence.map((item,index)=><article key={`${item.knowledge_id}-${index}`}><p>{turn.answer.kind === 'generated' && `[${index + 1}] `}{item.filename} · {location(item.locator)}</p><blockquote>{item.excerpt}</blockquote>{item.guest_visible !== false ? <a href={`/api/guest/sources/${encodeURIComponent(item.source_id)}/download`}>下载来源</a> : <small>原件未对访客开放下载</small>}</article>)}
    </div></div>)}</div>
    {busy && <div role="status"><p>{stage === 'reading' ? '正在阅读资料…' : stage === 'answering' ? '正在整理回答…' : '正在思考…'}</p><ProgressSteps steps={steps} /></div>}
    <form onSubmit={ask}><label>问题<textarea value={question} onChange={event=>setQuestion(event.target.value)} maxLength={240} rows={3} placeholder="输入问题或继续追问" /></label><button disabled={busy || !project || question.trim().length < 2}>{busy ? '正在思考…' : '发送'}</button></form>
    {error && <p role="status">{error}</p>}
  </section>
}
