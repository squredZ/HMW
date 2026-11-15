import { FormEvent, useEffect, useState } from 'react'

type Project = {id:string; name:string}
type Locator = {kind?:string; start_line?:number; end_line?:number; index?:number; block_kind?:string; slide?:number; element?:number}
type Evidence = {knowledge_id:string; source_id:string; filename:string; locator:Locator; excerpt:string}
type Answer = {kind:'extractive' | 'insufficient_evidence'; message:string; search_limited:boolean; unavailable_sources?:number; evidence:Evidence[]}

function location(locator:Locator):string {
  if (locator.kind === 'docx') return `${locator.block_kind === 'table' ? '表格' : '段落'} ${locator.index}`
  if (locator.kind === 'pptx') return `幻灯片 ${locator.slide} · 元素 ${locator.element}`
  return `第 ${locator.start_line} 行`
}

export default function GuestQuestions({projects}:{projects:Project[]}) {
  const [project,setProject] = useState('')
  const [question,setQuestion] = useState('')
  const [answer,setAnswer] = useState<Answer | null>(null)
  const [error,setError] = useState('')
  const [busy,setBusy] = useState(false)

  useEffect(() => {
    if (project && !projects.some(item => item.id === project)) {
      setProject(''); setAnswer(null)
    }
  }, [project, projects])

  async function ask(event:FormEvent) {
    event.preventDefault()
    if (!project || question.trim().length < 2) return
    setBusy(true); setAnswer(null); setError('')
    try {
      const response = await fetch('/api/guest/questions', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:project,question:question.trim()})})
      if (!response.ok) throw new Error(response.status === 401 ? '临时会话已失效，请刷新页面。' : '无法查找该项目的依据，请确认项目仍可访问。')
      setAnswer(await response.json())
    } catch (caught) { setError((caught as Error).message) } finally { setBusy(false) }
  }

  return <section className="card question-panel"><h2>问一问</h2><p>从已分享的项目知识中查找相关原文。结果仅供核对，不会自动生成结论。</p>
    <form onSubmit={ask}><label>问答项目<select value={project} disabled={busy} onChange={event=>{setProject(event.target.value);setAnswer(null)}}><option value="">选择项目</option>{projects.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>问题<textarea value={question} onChange={event=>setQuestion(event.target.value)} maxLength={240} rows={3} placeholder="输入你想了解的问题，例如：项目接口规范是什么？" /></label><button disabled={busy || !project || question.trim().length < 2}>查找依据</button>
    </form>
    {error && <p role="status">{error}</p>}{answer && <div><p>{answer.message}</p>{answer.search_limited && <p>本次仅检索了部分资料，请缩小范围或补充关键词。</p>}{(answer.unavailable_sources ?? 0) > 0 && <p>部分来源暂时无法校验，已从结果中排除。</p>}
      {answer.evidence.map((item,index)=><article key={`${item.knowledge_id}-${index}`}><p>{item.filename} · {location(item.locator)}</p><blockquote>{item.excerpt}</blockquote><a href={`/api/guest/sources/${encodeURIComponent(item.source_id)}/download`}>下载来源</a></article>)}
    </div>}
  </section>
}
