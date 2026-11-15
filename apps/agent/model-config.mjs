// DeepSeek defaults to thinking when the request omits its thinking setting.
export function configureModel(template, settings) {
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
