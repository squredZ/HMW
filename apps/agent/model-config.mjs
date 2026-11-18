// DeepSeek defaults to thinking when the request omits its thinking setting.
export function configureModel(models, settings) {
  const template = models.getModel('deepseek', settings.model) ?? models.getModel('deepseek', 'deepseek-flash')
  if (!template) throw new Error('provider_unavailable')
  return {...template, id:settings.model, name:settings.model, baseUrl:settings.api_url,
    maxTokens:settings.max_output_tokens ?? 1024,
    contextWindow:settings.context_window_tokens ?? template.contextWindow,
    reasoning:true,
    thinkingLevelMap:{...template.thinkingLevelMap, low:'low', high:'high', max:'max'}}
}

export function requireProxySupport(env = process.env, version = process.versions.node) {
  if (env.HMW_PROXY_REQUIRED !== '1') return
  const [major, minor] = version.split('.').map(Number)
  if (!(major >= 24 || (major === 22 && minor >= 21))) throw new Error('proxy_runtime_unsupported')
}

// Budget the whole Pi context on every model turn, including fresh tool results.
// Counting each serialized UTF-8 byte as one token is a conservative estimate.
export function budgetedStream(stream, settings = {}, onLimit = () => {}) {
  const outputTokens = settings.max_output_tokens ?? 1024
  const inputBudget = Math.min(settings.max_context_bytes ?? 200000,
    (settings.context_window_tokens ?? 131072) - outputTokens)
  return (model, context, options) => {
    if (Buffer.byteLength(JSON.stringify(context), 'utf8') > inputBudget) {
      onLimit()
      throw new Error('context_limit')
    }
    return stream(model, context, {...options, maxTokens: outputTokens})
  }
}
