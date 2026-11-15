// Disposable integration probe: Pi loop with a scripted model and no I/O tools.
import { Agent } from '@earendil-works/pi-agent-core'
import { createModels } from '@earendil-works/pi-ai'
import { fauxProvider, fauxAssistantMessage } from '@earendil-works/pi-ai/providers/faux'

const faux = fauxProvider()
const models = createModels()
models.setProvider(faux.provider)
faux.setResponses([fauxAssistantMessage('示例回答 [来源: sample.md:1]')])
const agent = new Agent({initialState: {model: faux.getModel(), systemPrompt: '仅基于提供的证据回答。', tools: []}, streamFn: models.streamSimple.bind(models)})
await agent.prompt('脱敏样例: sample.md 第 1 行，项目代号为 A。项目代号是什么？')
const result = agent.state.messages.at(-1)
if (!result?.content?.some(item => item.type === 'text' && item.text.includes('sample.md:1'))) throw Error('missing citation')
console.log(JSON.stringify({model: 'faux', tools: agent.state.tools.length, answer: result.content}))
