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
