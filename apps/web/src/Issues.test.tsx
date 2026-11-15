// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import GuestIssues from './GuestIssues'
import OwnerIssues from './OwnerIssues'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('lets a visitor submit a problem to a shared project', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({id:'i1',state:'new'}),{status:201}))
  vi.stubGlobal('fetch', fetcher)
  render(<GuestIssues projects={[{id:'p1',name:'项目一'}]} />)
  fireEvent.change(screen.getByLabelText('问题所属项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('问题标题'),{target:{value:'接口异常'}})
  fireEvent.change(screen.getByLabelText('问题描述'),{target:{value:'请求失败'}})
  fireEvent.click(screen.getByRole('button',{name:'提交问题'}))
  await screen.findByText('问题已提交给负责人。')
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({project_id:'p1',title:'接口异常',description:'请求失败'})
})

it('lets the owner review and resolve an issue', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:'i1',project_name:'项目一',title:'接口异常',description:'请求失败',state:'new'}],has_more:false})))
    .mockResolvedValueOnce(new Response(JSON.stringify({id:'i1',state:'resolved'})))
  vi.stubGlobal('fetch', fetcher)
  render(<OwnerIssues csrf="token" />)
  fireEvent.click(screen.getByRole('button',{name:'加载问题'}))
  await screen.findByText('请求失败')
  fireEvent.click(screen.getByRole('button',{name:'标记已解决'}))
  await screen.findByText('问题已标记为已解决。')
  expect(fetcher.mock.calls[1][0]).toBe('/api/issues/i1/resolve')
})
