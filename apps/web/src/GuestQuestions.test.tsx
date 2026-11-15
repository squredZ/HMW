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
  fireEvent.click(screen.getByRole('button',{name:'查找依据'}))
  await screen.findByText('接口版本为 V2。')
  expect(screen.getByText(/设计.md · 第 2 行/)).toBeTruthy()
  expect(screen.getByRole('link',{name:'下载来源'}).getAttribute('href')).toBe('/api/guest/sources/s1/download')
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({project_id:'p1',question:'接口版本是什么？'})
})

it('explains when the search is limited and gives no invented answer', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'insufficient_evidence',message:'未找到可核对的匹配依据。',search_limited:true,evidence:[]}),{status:200})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'未知信息'}})
  fireEvent.click(screen.getByRole('button',{name:'查找依据'}))
  await screen.findByText('未找到可核对的匹配依据。')
  expect(screen.getByText('本次仅检索了部分资料，请缩小范围或补充关键词。')).toBeTruthy()
})

it('informs visitors when a matching source cannot be verified', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'insufficient_evidence',message:'未找到可核对的匹配依据。',search_limited:false,unavailable_sources:1,evidence:[]}),{status:200})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'查找依据'}))
  await screen.findByText('部分来源暂时无法校验，已从结果中排除。')
})

it('labels generated answers and shows numbered source evidence', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'generated',message:'版本是 V2 [1]。',citations:[1],search_limited:false,evidence:[{knowledge_id:'k1',source_id:'s1',filename:'设计.md',locator:{start_line:2},excerpt:'版本是 V2。'}]}))))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'查找依据'}))
  await screen.findByText('版本是 V2 [1]。')
  expect(screen.getByText('AI 生成，请核对来源。')).toBeTruthy()
  expect(screen.getByText(/\[1\] 设计.md/)).toBeTruthy()
})

it('asks the visitor to retry when sources change during generation', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response('{}',{status:409})))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'查找依据'}))
  await screen.findByText('资料的可见范围或内容已发生变化，请重新提问。')
})

it('explains when the agent assessed the materials but could not answer', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({kind:'insufficient_evidence',message:'现有资料不足以回答，请补充信息或联系负责人。',agent_invoked:true,search_limited:false,evidence:[]}))))
  render(<GuestQuestions projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问答项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'接口版本'}})
  fireEvent.click(screen.getByRole('button',{name:'查找依据'}))
  await screen.findByText('Agent 已检查可用资料，未找到足够依据。')
})
