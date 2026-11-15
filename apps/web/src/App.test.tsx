// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import App from './App'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('personal status', () => {
  it('signs in, saves a draft, and publishes the version returned by the server', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({csrf_token: 'token'}), {status: 200}))
      .mockResolvedValueOnce(new Response('null', {status: 200}))
      .mockResolvedValueOnce(new Response(JSON.stringify({id:'s1', version:1, state:'draft', focus:'上线', progress:20}), {status:200}))
      .mockResolvedValueOnce(new Response(JSON.stringify({id:'s1', version:2, state:'published', focus:'上线', progress:20}), {status:200}))
    vi.stubGlobal('fetch', fetcher)
    render(<App />)
    fireEvent.change(screen.getByLabelText('账号'), {target: {value: 'owner'}})
    fireEvent.change(screen.getByLabelText('密码'), {target: {value: 'password'}})
    fireEvent.click(screen.getByRole('button', {name:'登录'}))
    await screen.findByLabelText('当前重点')
    fireEvent.change(screen.getByLabelText('当前重点'), {target: {value: '上线'}})
    fireEvent.change(screen.getByLabelText('进度（%）'), {target: {value: '20'}})
    fireEvent.click(screen.getByRole('button', {name:'保存草稿'}))
    await screen.findByText('草稿已保存')
    fireEvent.click(screen.getByRole('button', {name:'发布'}))
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4))
    expect(JSON.parse(fetcher.mock.calls[3][1].body)).toEqual({expected_version:1})
    await screen.findByText('已发布')
  })
})
