// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import SkillManager from './SkillManager'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('loads and retires a project skill', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:'s1',name:'链路排查',module:'通信',content:'检查状态'}],has_more:false})))
    .mockResolvedValueOnce(new Response(JSON.stringify({id:'s1',active:false})))
  vi.stubGlobal('fetch', fetcher)
  render(<SkillManager csrf="token" project="p1" module="通信" />)
  fireEvent.click(screen.getByRole('button',{name:'加载 Skill'}))
  fireEvent.click(await screen.findByText('链路排查 · 通信'))
  fireEvent.click(screen.getByRole('button',{name:'停用 链路排查'}))
  expect(await screen.findByText('Skill 已停用')).toBeTruthy()
  expect(fetcher.mock.calls[1][0]).toBe('/api/projects/p1/skills/s1')
  expect(fetcher.mock.calls[1][1].method).toBe('DELETE')
})
