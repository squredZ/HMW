import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fauxProvider, fauxAssistantMessage, fauxToolCall } from '@earendil-works/pi-ai/providers/faux'
import { createModels } from '@earendil-works/pi-ai'
import { runAnalysisAgent } from '../analysis.mjs'

test('analysis agent invokes host read tools', async () => {
  const faux = fauxProvider(), models = createModels(); models.setProvider(faux.provider)
  faux.setResponses([
    fauxAssistantMessage([fauxToolCall('list_resources',{kind:'code',path:'',offset:0})], {stopReason:'toolUse'}),
    fauxAssistantMessage([fauxToolCall('read_resource',{kind:'code',path:'a.txt',offset:0})], {stopReason:'toolUse'}),
    fauxAssistantMessage('{"answer":"ok","citations":[]}'),
  ])
  const calls=[]
  const result=await runAnalysisAgent({question:'分析'}, faux.getModel(), models.streamSimple.bind(models), ()=> 'key', async (name,args)=>{calls.push([name,args]); return {text:'ok'}})
  assert.deepEqual(calls.map(call=>call[0]),['list_resources','read_resource'])
  assert.equal(result.answer,'ok')
})

test('requests JSON output and accepts an uncited analysis report', async () => {
  const faux = fauxProvider()
  const models = createModels()
  models.setProvider(faux.provider)
  faux.setResponses([fauxAssistantMessage('{"answer":"日志显示连接失败。"}')])
  const result = await runAnalysisAgent({question:'分析'}, faux.getModel(), (model, context, options) => {
    assert.deepEqual(options.onPayload({model:'test'}).response_format, {type:'json_object'})
    return models.streamSimple(model, context, options)
  }, () => 'key', async () => ({}))
  assert.equal(result.answer, '日志显示连接失败。')
  assert.deepEqual(result.citations, [])
})
