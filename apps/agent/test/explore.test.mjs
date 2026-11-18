import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fauxProvider, fauxAssistantMessage, fauxToolCall } from '@earendil-works/pi-ai/providers/faux'
import { createModels } from '@earendil-works/pi-ai'
import { runExploringAgent } from '../explore.mjs'

test('agent lists and reads knowledge before answering an unrelated phrasing', async () => {
  const faux=fauxProvider(); const models=createModels(); models.setProvider(faux.provider)
  faux.setResponses([
    fauxAssistantMessage([fauxToolCall('list_knowledge',{offset:0})],{stopReason:'toolUse'}),
    fauxAssistantMessage([fauxToolCall('read_knowledge',{id:'doc',offset:0})],{stopReason:'toolUse'}),
    fauxAssistantMessage('{"answer":"它能够同步数据 [1]。","citations":[1]}'),
  ])
  const calls=[]
  const result=await runExploringAgent({question:'这个项目有什么用？'},faux.getModel(),models.streamSimple.bind(models),()=> 'key',async (name,args)=>{
    calls.push(name)
    return name === 'list_knowledge' ? {documents:[{id:'doc',name:'介绍'}],next_offset:null} : {number:1,text:'产品功能：可靠的数据同步。',next_offset:null}
  })
  assert.deepEqual(calls,['list_knowledge','read_knowledge'])
  assert.deepEqual(result.citations,[1])
})

test('agent is instructed to answer general questions without requiring project knowledge', async () => {
  const faux=fauxProvider(); const models=createModels(); models.setProvider(faux.provider)
  faux.setResponses([fauxAssistantMessage('{"answer":"幂等性是指多次执行与一次执行的效果相同。","citations":[]}')])
  let context
  const result=await runExploringAgent({question:'什么是幂等性？'},faux.getModel(),(model,seen,options)=>{
    context=seen
    return models.streamSimple(model,seen,options)
  },()=> 'key',async ()=>{ throw new Error('general answer should not require a tool') })
  assert.match(JSON.stringify(context),/通用问题.*模型.*知识/)
  assert.match(JSON.stringify(context),/项目事实.*引用/)
  assert.deepEqual(result.citations,[])
  assert.match(result.answer,/幂等/)
})

test('model output limit is configurable and forwarded to each model call', async () => {
  const faux=fauxProvider(); const models=createModels(); models.setProvider(faux.provider)
  faux.setResponses([fauxAssistantMessage('{"answer":"可以。","citations":[]}')])
  let selected
  await runExploringAgent({question:'请说明',settings:{max_output_tokens:2048,max_context_bytes:200000}},faux.getModel(),(model,context,options)=>{
    selected=options.maxTokens
    return models.streamSimple(model,context,options)
  },()=> 'key',async ()=>{})
  assert.equal(selected,2048)
})

test('context byte budget prevents oversized requests from reaching the provider', async () => {
  const faux=fauxProvider(); let called=false
  await assert.rejects(runExploringAgent({question:'测试', history:[{question:'旧问题',answer:'字'.repeat(3000)}],settings:{max_context_bytes:1024}},faux.getModel(),()=>{called=true},()=> 'key',async ()=>{}))
  assert.equal(called,false)
})

test('DeepSeek off mode explicitly disables thinking in the provider payload', async () => {
  const {deepseekProvider}=await import('@earendil-works/pi-ai/providers/deepseek')
  const {configureModel}=await import('../model-config.mjs')
  const models=createModels(); models.setProvider(deepseekProvider())
  const model=configureModel(models,{model:'deepseek-v4-pro',api_url:'https://api.deepseek.com',max_output_tokens:2048,context_window_tokens:1048576})
  let payload
  await assert.rejects(runExploringAgent({question:'你好',settings:{reasoning_effort:'off',max_output_tokens:2048}},model,(selected,context,options)=>models.streamSimple(selected,context,{...options,onPayload: body=>{payload=body;throw new Error('stop before network')}}),()=> 'key',async ()=>{}))
  assert.deepEqual(payload.thinking,{type:'disabled'})
  assert.equal(payload.max_tokens,2048)
})

test('proxy mode refuses unsupported Node versions before connecting', async () => {
  const {requireProxySupport}=await import('../model-config.mjs')
  assert.throws(()=>requireProxySupport({HMW_PROXY_REQUIRED:'1'},'22.19.0'))
  assert.doesNotThrow(()=>requireProxySupport({HMW_PROXY_REQUIRED:'1'},'22.21.0'))
  assert.doesNotThrow(()=>requireProxySupport({HMW_PROXY_REQUIRED:'1'},'24.0.0'))
  assert.doesNotThrow(()=>requireProxySupport({},'22.19.0'))
})

for (const effort of ['low','high','max']) {
  test(`DeepSeek ${effort} mode forwards the requested thinking effort`, async () => {
    const {deepseekProvider}=await import('@earendil-works/pi-ai/providers/deepseek')
    const {configureModel}=await import('../model-config.mjs')
    const models=createModels(); models.setProvider(deepseekProvider())
    const model=configureModel(models,{model:'deepseek-v4-pro',api_url:'https://api.deepseek.com'})
    let payload
    await assert.rejects(runExploringAgent({question:'你好',settings:{reasoning_effort:effort}},model,(selected,context,options)=>models.streamSimple(selected,context,{...options,onPayload:body=>{payload=body;throw new Error('stop before network')}}),()=> 'key',async ()=>{}))
    assert.deepEqual(payload.thinking,{type:'enabled'})
    assert.equal(payload.reasoning_effort,effort)
  })
}

test('context byte budget is checked again after a knowledge tool result', async () => {
  const faux=fauxProvider(); const models=createModels(); models.setProvider(faux.provider)
  faux.setResponses([fauxAssistantMessage([fauxToolCall('read_knowledge',{id:'doc',offset:0})],{stopReason:'toolUse'})])
  let calls=0
  await assert.rejects(runExploringAgent({question:'介绍项目',settings:{max_context_bytes:4000}},faux.getModel(),(model,context,options)=>{
    calls++
    return models.streamSimple(model,context,options)
  },()=> 'key',async ()=>({number:1,text:'文'.repeat(3000),next_offset:null})))
  assert.equal(calls,1)
})


test('reserves output space in a small model window before calling the provider', async () => {
  const faux = fauxProvider()
  let calls = 0
  const settings = {context_window_tokens: 4096, max_output_tokens: 2048, max_context_bytes: 200000}
  await assert.rejects(runExploringAgent({question:'字'.repeat(1000), settings},
    faux.getModel(), () => { calls++ }, () => 'key', async () => {}))
  assert.equal(calls, 0)
})

test('V4 Pro uses its own model capabilities', async () => {
  const {deepseekProvider} = await import('@earendil-works/pi-ai/providers/deepseek')
  const {configureModel} = await import('../model-config.mjs')
  const models = createModels()
  models.setProvider(deepseekProvider())
  const model = configureModel(models, {model:'deepseek-v4-pro', api_url:'https://api.deepseek.com'})
  assert.deepEqual(model.input, ['text'])
})
