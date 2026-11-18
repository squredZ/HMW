// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import GuestQuestions from './GuestQuestions'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function resultResponse(data:unknown): Response {
  return new Response(`data: ${JSON.stringify({type:'result',data})}\n\n`)
}

it('asks within a selected project and shows an exact excerpt and source', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(resultResponse({
    kind:'extractive', message:'以下是可能相关的原文，请结合上下文判断。',search_limited:false,
    evidence:[{knowledge_id:'k1',source_id:'s1',filename:'设计.md',locator:{start_line:2,end_line:2},excerpt:'接口版本为 V2。'}]
  }))
  vi.stubGlobal('fetch',fetcher)
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本是什么？'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('接口版本为 V2。')
  expect(screen.getByText(/设计.md · 第 2 行/)).toBeTruthy()
  expect(screen.getByRole('link',{name:'下载来源'}).getAttribute('href')).toBe('/api/guest/sources/s1/download')
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({project_id:'p1',question:'接口版本是什么？'})
})

it('explains when the search is limited and gives no invented answer', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(resultResponse({kind:'insufficient_evidence',message:'未找到可核对的匹配依据。',search_limited:true,evidence:[]})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'未知信息'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('未找到可核对的匹配依据。')
  expect(screen.getByText('本次仅检索了部分资料，请缩小范围或补充关键词。')).toBeTruthy()
})

it('informs visitors when a matching source cannot be verified', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(resultResponse({kind:'insufficient_evidence',message:'未找到可核对的匹配依据。',search_limited:false,unavailable_sources:1,evidence:[]})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('部分来源暂时无法校验，已从结果中排除。')
})

it('labels generated answers and shows numbered source evidence', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(resultResponse({kind:'generated',message:'版本是 V2 [1]。',citations:[1],search_limited:false,evidence:[{knowledge_id:'k1',source_id:'s1',filename:'设计.md',locator:{start_line:2},excerpt:'版本是 V2。'}]})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('版本是 V2 [1]。')
  expect(screen.getByText('AI 生成，请核对来源。')).toBeTruthy()
  expect(screen.getByText(/\[1\] 设计.md/)).toBeTruthy()
})

it('asks the visitor to retry when sources change during generation', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response('{}',{status:409})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('对话正在处理或资料已变化，请稍后重试。')
})

it('explains when the agent assessed the materials but could not answer', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(resultResponse({kind:'insufficient_evidence',message:'现有资料不足以回答，请补充信息或联系负责人。',agent_invoked:true,search_limited:false,evidence:[]})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('Agent 已检查可用资料，未找到足够依据。')
})

it('keeps multiple turns in one conversation and starts a new one on request', async () => {
  const fetcher=vi.fn().mockImplementation(async () => resultResponse({kind:'generated',message:'回答 [1]',agent_invoked:true,search_limited:false,evidence:[{knowledge_id:'k1',source_id:'s1',filename:'内部.txt',guest_visible:false,locator:{start_line:1},excerpt:'资料'}]}))
  vi.stubGlobal('fetch', fetcher)
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'第一个问题'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('第一个问题')
  expect(screen.queryByRole('link',{name:'下载来源'})).toBeNull()
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'后续问题'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('后续问题')
  const first=JSON.parse(fetcher.mock.calls[0][1].body)
  const second=JSON.parse(fetcher.mock.calls[1][1].body)
  expect(first.conversation_id).toBeTruthy()
  expect(second.conversation_id).toBe(first.conversation_id)
  fireEvent.click(screen.getByRole('button',{name:'开始新对话'}))
  expect(screen.queryByText('第一个问题')).toBeNull()
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'新问题'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('新问题')
  expect(JSON.parse(fetcher.mock.calls[2][1].body).conversation_id).not.toBe(first.conversation_id)
})

it('explains a context limit when agent generation fails', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(resultResponse({kind:'extractive',message:'相关原文',agent_invoked:true,agent_unavailable:true,agent_error:'context_limit',search_limited:false,evidence:[]})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'介绍项目'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText(/对话上下文超过限制/)
})

it.each([
  ['timeout', '模型响应超时，请稍后重试。'],
  ['busy', '问答请求较多，请稍后重试。'],
  ['unavailable', '模型暂不可用，请稍后重试。'],
])('shows the %s failure without claiming an extract exists', async (reason, message) => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(resultResponse({kind:'insufficient_evidence',message:'未找到依据。',agent_invoked:true,agent_unavailable:true,agent_error:reason,search_limited:false,evidence:[]})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'介绍项目'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText(message)
  expect(screen.queryByText(/以下为原文检索结果/)).toBeNull()
})

it('shows streamed progress before the answer, including split UTF-8 events', async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({start(value) { controller = value }})
  const fetcher = vi.fn().mockResolvedValue(new Response(stream))
  vi.stubGlobal('fetch', fetcher)
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  const encoder = new TextEncoder()
  controller.enqueue(encoder.encode('data: {"type":"progress","stage":"reading"}\n\n'))
  await screen.findByText('正在阅读资料…')
  expect(fetcher.mock.calls[0][0]).toBe('/api/guest/questions/stream')
  const bytes = encoder.encode('data: '+JSON.stringify({type:'result',data:{kind:'generated',message:'版本是 V2。',search_limited:false,evidence:[]}})+'\n\n')
  const split = bytes.indexOf(0xe7) + 1
  controller.enqueue(bytes.slice(0, split)); controller.enqueue(bytes.slice(split)); controller.close()
  await screen.findByText('版本是 V2。')
  expect(screen.queryByText('正在阅读资料…')).toBeNull()
})

it('aborts the active stream on unmount', async () => {
  const fetcher = vi.fn().mockImplementation((_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  }))
  vi.stubGlobal('fetch', fetcher)
  const {unmount} = render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal
  unmount()
  expect(signal.aborted).toBe(true)
})

it('ignores unknown stages and hides stream error details', async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({start(value) { controller = value }}))))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  controller.enqueue(new TextEncoder().encode('data: {"type":"progress","stage":"secret"}\n\ndata: {"type":"error","message":"secret details"}\n\n'))
  await screen.findByText('问答处理失败，请稍后重试。')
  expect(screen.queryByText(/secret/)).toBeNull()
})

it('reports a truncated stream without adding a conversation turn', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('data: {"type":"progress","stage":"answering"}\n\n')))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('问答连接中断，请稍后重试。')
  expect(screen.queryByText('接口版本', {selector:'.conversation-user p'})).toBeNull()
})

it('keeps a bounded, sanitized history of progress after the answer', async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({start(value) { controller = value }}))))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  const send = (value:unknown) => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`))
  send({type:'progress',stage:'thinking'})
  send({type:'progress',stage:'reading',content:'秘密资料'})
  send({type:'progress',stage:'reading'})
  send({type:'progress',stage:'answering'})
  await screen.findByText('阅读资料（2 次）')
  expect(screen.getByText('思考问题')).toBeTruthy()
  expect(screen.getByText('整理回答')).toBeTruthy()
  expect(screen.queryByText(/秘密资料/)).toBeNull()
  send({type:'result',data:{kind:'generated',message:'回答完成',search_limited:false,evidence:[]}})
  controller.close()
  await screen.findByText('回答完成')
  expect(screen.getByText('阅读资料（2 次）')).toBeTruthy()
  expect(screen.getByText('整理回答')).toBeTruthy()
})

it('limits the retained progress list to the most recent 30 steps', async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({start(value) { controller = value }}))))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  const events = Array.from({length:40},(_, index) => `data: ${JSON.stringify({type:'progress',stage:index % 2 ? 'reading' : 'thinking'})}\n\n`).join('')
  controller.enqueue(new TextEncoder().encode(events))
  await screen.findByText('正在阅读资料…')
  expect(screen.getAllByRole('listitem')).toHaveLength(30)
  controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({type:'result',data:{kind:'generated',message:'已完成',search_limited:false,evidence:[]}})}\n\n`))
  controller.close()
  await screen.findByText('已完成')
  fireEvent.click(screen.getByText('查看处理过程'))
  expect(screen.getAllByRole('listitem')).toHaveLength(30)
})

it('stops reading an oversized incomplete event', async () => {
  const cancel = vi.fn()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(new TextEncoder().encode('data: ' + 'x'.repeat(1024 * 1024 + 1))) }, cancel,
  }))))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('问答处理失败，请稍后重试。')
  expect(cancel).toHaveBeenCalled()
})

it('stops reading malformed event data', async () => {
  const cancel = vi.fn()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(new TextEncoder().encode('data: {invalid}\n\n')) }, cancel,
  }))))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('问答处理失败，请稍后重试。')
  expect(cancel).toHaveBeenCalled()
})
