import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fauxProvider, fauxAssistantMessage, fauxToolCall } from '@earendil-works/pi-ai/providers/faux'
import { createModels } from '@earendil-works/pi-ai'
import { runAgent } from '../readonly.mjs'

test('Pi has no tools and receives only question and evidence', async () => {
  const faux = fauxProvider()
  const models = createModels()
  models.setProvider(faux.provider)
  faux.setResponses([fauxAssistantMessage('{"answer":"版本是 V2 [1]。","citations":[1]}')])
  let seen
  const stream = (model, context, options) => {
    seen = context
    return models.streamSimple(model, context, options)
  }
  const result = await runAgent({question:'版本是什么', evidence:[{excerpt:'版本是 V2。'}]}, faux.getModel(), stream, ()=>'key')
  assert.ok(!seen.tools || seen.tools.length === 0)
  assert.match(JSON.stringify(seen), /版本是 V2。/)
  assert.equal(result.answer, '版本是 V2 [1]。')
  assert.deepEqual(result.citations, [1])
})


test('a tool call emitted by the model cannot run a write action', async () => {
  const faux = fauxProvider()
  const models = createModels()
  models.setProvider(faux.provider)
  faux.setResponses([fauxAssistantMessage([fauxToolCall('write_knowledge', {content:'modified'})], {stopReason:'toolUse'})])
  await assert.rejects(runAgent({question:'修改知识', evidence:[{excerpt:'只读内容'}]}, faux.getModel(), models.streamSimple.bind(models), ()=>'key'))
})
