// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import GuestQuestions from './GuestQuestions'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('asks within a selected project and shows an exact excerpt and source', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
    kind:'extractive', message:'以下是可能相关的原文，请结合上下文判断。',search_limited:false,
    evidence:[{knowledge_id:'k1',source_id:'s1',filename:'设计.md',locator:{start_line:2,end_line:2},excerpt:'接口版本为 V2。'}]
  }),{status:200}))
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
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'insufficient_evidence',message:'未找到可核对的匹配依据。',search_limited:true,evidence:[]}),{status:200})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'未知信息'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('未找到可核对的匹配依据。')
  expect(screen.getByText('本次仅检索了部分资料，请缩小范围或补充关键词。')).toBeTruthy()
})

it('informs visitors when a matching source cannot be verified', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'insufficient_evidence',message:'未找到可核对的匹配依据。',search_limited:false,unavailable_sources:1,evidence:[]}),{status:200})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('部分来源暂时无法校验，已从结果中排除。')
})

it('labels generated answers and shows numbered source evidence', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'generated',message:'版本是 V2 [1]。',citations:[1],search_limited:false,evidence:[{knowledge_id:'k1',source_id:'s1',filename:'设计.md',locator:{start_line:2},excerpt:'版本是 V2。'}]}))))
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
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'insufficient_evidence',message:'现有资料不足以回答，请补充信息或联系负责人。',agent_invoked:true,search_limited:false,evidence:[]}))))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText('Agent 已检查可用资料，未找到足够依据。')
})

it('keeps multiple turns in one conversation and starts a new one on request', async () => {
  const fetcher=vi.fn().mockImplementation(async () => new Response(JSON.stringify({kind:'generated',message:'回答 [1]',agent_invoked:true,search_limited:false,evidence:[{knowledge_id:'k1',source_id:'s1',filename:'内部.txt',guest_visible:false,locator:{start_line:1},excerpt:'资料'}]})))
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
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'extractive',message:'相关原文',agent_invoked:true,agent_unavailable:true,agent_error:'context_limit',search_limited:false,evidence:[]}))))
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
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'insufficient_evidence',message:'未找到依据。',agent_invoked:true,agent_unavailable:true,agent_error:reason,search_limited:false,evidence:[]}))))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'介绍项目'}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
  await screen.findByText(message)
  expect(screen.queryByText(/以下为原文检索结果/)).toBeNull()
})
