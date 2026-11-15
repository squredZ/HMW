// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import KnowledgeManager from './KnowledgeManager'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('browses knowledge and retires a shared version', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:'k1',filename:'指南.md',state:'published',guest_visible:true}],has_more:false})))
    .mockResolvedValueOnce(new Response(JSON.stringify({id:'k1',state:'published',content:'<script>内容</script>',guest_visible:true,source:{id:'s1',filename:'指南.md'}})))
    .mockResolvedValueOnce(new Response(JSON.stringify({version_id:'k1',state:'retired'})))
  vi.stubGlobal('fetch', fetcher)
  render(<KnowledgeManager csrf="token" projects={[{id:'p1',name:'项目一',role:'owner'}]} />)
  fireEvent.change(screen.getByLabelText('管理项目'), {target:{value:'p1'}})
  fireEvent.click(screen.getByRole('button',{name:'加载知识'}))
  fireEvent.click(await screen.findByRole('button',{name:'查看 指南.md'}))
  await screen.findByText('<script>内容</script>')
  expect(document.querySelector('script')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'停用知识'}))
  await screen.findByText('知识已停用，访客无法继续查看。')
  expect(fetcher.mock.calls[2][0]).toBe('/api/knowledge/k1/retire')
  expect(screen.getByRole('link',{name:'下载原文件'}).getAttribute('href')).toBe('/api/sources/s1/download')
})

it('shows source review warnings and locations before publishing a draft', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:'k2',filename:'材料.docx',state:'draft',guest_visible:false}],has_more:false})))
    .mockResolvedValueOnce(new Response(JSON.stringify({id:'k2',state:'draft',content:'正文',guest_visible:false,source:{id:'s2',filename:'材料.docx'},locator:{kind:'docx',warnings:['comments_present'],anchors:[{line:1,kind:'table',index:2}]}})))
  vi.stubGlobal('fetch', fetcher)
  render(<KnowledgeManager csrf="token" projects={[{id:'p1',name:'项目一',role:'owner'}]} />)
  fireEvent.change(screen.getByLabelText('管理项目'), {target:{value:'p1'}})
  fireEvent.click(screen.getByRole('button',{name:'加载知识'}))
  fireEvent.click(await screen.findByRole('button',{name:'查看 材料.docx'}))
  await screen.findByText('原文件包含批注，请人工核对。')
  expect(screen.getByText('第 1 行：表格 2')).toBeTruthy()
})
