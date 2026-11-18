// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import GuestIssues from './GuestIssues'
import OwnerIssues from './OwnerIssues'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('submits a visitor issue with its required log and shows the shared list', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[],has_more:false})))
    .mockResolvedValueOnce(new Response(JSON.stringify({id:'i1'}),{status:201}))
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:'i1',project_id:'p1',project_name:'项目一',module:'接口',description:'请求失败',log_filename:'app.log',state:'new',analysis:null}],has_more:false})))
  vi.stubGlobal('fetch', fetcher)
  render(<GuestIssues projects={[{id:'p1',name:'项目一'}]} />)
  await screen.findByText('暂无问题。')
  fireEvent.change(screen.getByLabelText('问题所属项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('功能模块'),{target:{value:'接口'}})
  fireEvent.change(screen.getByLabelText('问题现象'),{target:{value:'请求失败'}})
  expect((screen.getByRole('button',{name:'提交问题'}) as HTMLButtonElement).disabled).toBe(true)
  const log = new File(['error'], 'app.log', {type:'text/plain'})
  fireEvent.change(screen.getByLabelText('日志文件'),{target:{files:[log]}})
  fireEvent.click(screen.getByRole('button',{name:'提交问题'}))
  await screen.findByText('问题已提交。')
  expect(await screen.findByText('请求失败')).toBeTruthy()
  const body = fetcher.mock.calls[1][1].body as FormData
  expect(body.get('project_id')).toBe('p1')
  expect(body.get('module')).toBe('接口')
  expect(body.get('description')).toBe('请求失败')
  expect(body.get('log')).toBe(log)
})

it('reuses the submission key after a network failure and changes it when the issue changes', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[],has_more:false})))
    .mockRejectedValueOnce(new TypeError('network'))
    .mockRejectedValueOnce(new TypeError('network'))
    .mockRejectedValueOnce(new TypeError('network'))
  vi.stubGlobal('fetch', fetcher)
  render(<GuestIssues projects={[{id:'p1',name:'项目一'}]} />)
  await screen.findByText('暂无问题。')
  fireEvent.change(screen.getByLabelText('问题所属项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('功能模块'),{target:{value:'接口'}})
  fireEvent.change(screen.getByLabelText('问题现象'),{target:{value:'请求失败'}})
  fireEvent.change(screen.getByLabelText('日志文件'),{target:{files:[new File(['x'],'app.log')]}})
  fireEvent.click(screen.getByRole('button',{name:'提交问题'}))
  await screen.findByText('网络异常，提交结果未知，请直接重试。')
  fireEvent.click(screen.getByRole('button',{name:'提交问题'}))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3))
  await screen.findByText('网络异常，提交结果未知，请直接重试。')
  fireEvent.change(screen.getByLabelText('问题现象'),{target:{value:'新的问题'}})
  fireEvent.click(screen.getByRole('button',{name:'提交问题'}))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4))
  const keys = fetcher.mock.calls.slice(1).map(call => call[1].headers['Idempotency-Key'])
  expect(keys[0]).toMatch(/^[\da-f-]{36}$/)
  expect(keys[1]).toBe(keys[0])
  expect(keys[2]).not.toBe(keys[0])
})

it('queues analysis and displays the resulting report after polling', async () => {
  const issue = {id:'i1',project_name:'项目一',module:'接口',description:'请求失败',log_filename:'app.log',state:'new'}
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[issue],has_more:false})))
    .mockResolvedValueOnce(new Response('{}'))
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{...issue,analysis:{state:'queued'}}],has_more:false})))
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{...issue,analysis:{state:'succeeded',report:'分析报告'}}],has_more:false})))
  vi.stubGlobal('fetch', fetcher)
  render(<GuestIssues projects={[]} />)
  fireEvent.click(await screen.findByRole('button',{name:'分析问题'}))
  expect(await screen.findByText('分析报告', {}, {timeout:5000})).toBeTruthy()
  expect(fetcher.mock.calls[1][0]).toBe('/api/guest/issues/i1/analyze')
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

it('shows analysis failures for older issues without optional fields', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({items:[{id:'old',description:'旧问题',analysis:{state:'failed',reason:'日志无法读取'}}],has_more:false}))))
  render(<GuestIssues projects={[]} />)
  expect(await screen.findByText('旧问题')).toBeTruthy()
  expect(screen.getByText('失败原因：分析失败，请联系负责人检查后重试。')).toBeTruthy()
  expect((screen.getByRole('button',{name:'分析问题'}) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText('该问题没有日志，无法分析。')).toBeTruthy()
})

it('keeps polling an active analysis after repeated list failures', async () => {
  const issue = {id:'i1',description:'待分析',analysis:{state:'running'}}
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[issue],has_more:false})))
    .mockResolvedValueOnce(new Response('', {status:500}))
    .mockResolvedValueOnce(new Response('', {status:500}))
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{...issue,analysis:{state:'succeeded',report:'恢复后的报告'}}],has_more:false}))))
  render(<GuestIssues projects={[]} />)
  expect(await screen.findByText('恢复后的报告', {}, {timeout:8000})).toBeTruthy()
}, 10000)


it('pages through shared issues and polls the current page', async () => {
  const fetcher = vi.fn((url: string) => {
    if (url.includes('offset=50')) return Promise.resolve(new Response(JSON.stringify({items:[{id:'second',description:'第二页问题',log_filename:'second.log',analysis:{state:'running'}}],has_more:false})))
    return Promise.resolve(new Response(JSON.stringify({items:[{id:'first',description:'第一页问题'}],has_more:true})))
  })
  vi.stubGlobal('fetch', fetcher)
  render(<GuestIssues projects={[]} />)
  await screen.findByText('第一页问题')
  fireEvent.click(screen.getByRole('button',{name:'下一页'}))
  expect(await screen.findByText('第二页问题')).toBeTruthy()
  await new Promise(resolve => setTimeout(resolve, 2200))
  expect(fetcher.mock.calls.filter(call => call[0].includes('offset=50')).length).toBeGreaterThanOrEqual(2)
  fireEvent.click(screen.getByRole('button',{name:'上一页'}))
  expect(await screen.findByText('第一页问题')).toBeTruthy()
})

it('explains the upload size limit', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({items:[],has_more:false}))).mockResolvedValueOnce(new Response('', {status:413})))
  render(<GuestIssues projects={[{id:'p1',name:'项目一'}]} />)
  await screen.findByText('暂无问题。')
  fireEvent.change(screen.getByLabelText('问题所属项目'),{target:{value:'p1'}})
  fireEvent.change(screen.getByLabelText('功能模块'),{target:{value:'接口'}})
  fireEvent.change(screen.getByLabelText('问题现象'),{target:{value:'请求失败'}})
  fireEvent.change(screen.getByLabelText('日志文件'),{target:{files:[new File(['x'],'app.log')]}})
  fireEvent.click(screen.getByRole('button',{name:'提交问题'}))
  expect(await screen.findByText('日志文件不能超过 10 MB。')).toBeTruthy()
})

it('explains an analysis conflict', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:'i1',description:'问题',log_filename:'app.log'}],has_more:false}))).mockResolvedValueOnce(new Response('', {status:409})))
  render(<GuestIssues projects={[]} />)
  fireEvent.click(await screen.findByRole('button',{name:'分析问题'}))
  expect(await screen.findByText('问题缺少日志，或分析已在排队或运行中，请刷新列表。')).toBeTruthy()
})

it('explains analysis failures without exposing internal reason codes', async () => {
  const reasons = ['model_not_configured', 'code_root_unavailable', 'skill_root_unavailable', 'log_integrity_failed', 'context_limit', 'timeout', 'resource_unavailable', 'unexpected_code']
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({items:reasons.map((reason, index) => ({id:String(index),description:`问题 ${index}`,analysis:{state:'failed',reason}})),has_more:false}))))
  render(<GuestIssues projects={[]} />)
  expect(await screen.findByText('失败原因：分析模型未配置，请联系负责人配置模型。')).toBeTruthy()
  for (const message of ['项目代码目录不可用，请联系负责人检查配置。', '技能目录不可用，请联系负责人检查配置。', '日志校验失败，请重新提交问题和日志。', '分析内容超过模型上下文限制，请缩小日志范围后重试。', '分析超时，请稍后重试。', '分析所需资源不可用，请联系负责人检查。', '分析失败，请联系负责人检查后重试。']) {
    expect(screen.getByText(`失败原因：${message}`)).toBeTruthy()
  }
  expect(screen.queryByText(/unexpected_code/)).toBeNull()
})

it('shows paginated analysis usage and unknown historical usage', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:'i1',description:'问题',analysis:{id:'a2',state:'completed'}}],has_more:false})))
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:'a2',state:'completed',created_at:100,usage:{input:10,output:5,cache_read:2,cache_write:0,total:17}},{id:'a1',state:'failed',created_at:90,usage:null}],has_more:true})))
    .mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:'a0',state:'completed',created_at:80,usage:null}],has_more:false}))))
  render(<GuestIssues projects={[]} />)
  fireEvent.click(await screen.findByRole('button',{name:'查看分析记录'}))
  expect(await screen.findByText(/总计 17/)).toBeTruthy()
  expect(screen.getByText('Token 用量：未知')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'分析记录下一页'}))
  expect(await screen.findByText('第 2 页分析记录')).toBeTruthy()
})
