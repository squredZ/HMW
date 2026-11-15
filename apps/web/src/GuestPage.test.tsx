// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import GuestPage from './GuestPage'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('opens a temporary session and shows explicitly shared owner information', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({kind:'guest'}),{status:200}))
    .mockResolvedValueOnce(new Response(JSON.stringify({focus:'当前工作',progress:50,updated_at:'2026-11-15T00:00:00Z',allocations:[{project_name:'共享项目',week:'2026-11-09',unit:'hours',planned:4,actual:null}]}),{status:200}))
    .mockResolvedValueOnce(new Response(JSON.stringify([{id:'p1',name:'共享项目'}]),{status:200}))
    .mockResolvedValueOnce(new Response(JSON.stringify([{id:'k1',filename:'说明.md'}]),{status:200}))
    .mockResolvedValueOnce(new Response(JSON.stringify({id:'k1',content:'共享知识',locator:{start_line:1,end_line:1},source:{id:'s1',filename:'说明.md'}}),{status:200}))
  vi.stubGlobal('fetch',fetcher)
  render(<GuestPage onOwnerLogin={()=>{}} />)
  await screen.findByText('当前工作')
  expect(screen.getByText(/截至手动更新/).textContent).toContain('08:00')
  expect(screen.getByText(/计划 4/).textContent).toContain('实际 未填')
  fireEvent.click(screen.getByRole('button',{name:'项目知识'}))
  fireEvent.click(screen.getByRole('button',{name:'共享项目'}))
  await screen.findByRole('button',{name:'说明.md'})
  fireEvent.click(screen.getByRole('button',{name:'说明.md'}))
  await screen.findByText('共享知识')
  expect(screen.getByText('来源行 1–1')).toBeTruthy()
  expect(screen.queryByRole('button',{name:'保存草稿'})).toBeNull()
  expect(screen.getByRole('link',{name:'下载原文件'}).getAttribute('href')).toBe('/api/guest/sources/s1/download')
})

it('switches between questions, shared knowledge and owner status', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response('{}'))
    .mockResolvedValueOnce(new Response('', {status:404}))
    .mockResolvedValueOnce(new Response('[]')))
  render(<GuestPage onOwnerLogin={()=>{}} />)
  await screen.findByText('暂未发布工作状态。')
  expect(screen.getByRole('heading',{name:'问一问'})).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'项目知识'}))
  expect(screen.getByRole('heading',{name:'项目知识', level:2})).toBeTruthy()
  expect(screen.queryByRole('heading',{name:'问一问'})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'关于我'}))
  expect(screen.getByRole('heading',{name:'关于我 · 当前工作状态'})).toBeTruthy()
})
