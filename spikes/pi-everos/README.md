# Pi + EverOS 脱敏验证（临时试验）

目的：记录先前对两个上游包的临时验证。本目录不是产品集成，不应处理真实数据。MVP 不接入 EverOS，本探针不参与应用安装、启动或运行。

## 当前结果

- Pi `@earendil-works/pi-agent-core` / `pi-ai` 1.5.2：使用 Pi 自带 faux 模型运行 Agent，工具列表为空。输出是预先写好的模拟回答，不能证明检索、生成正确性或引用有效性。
- EverOS 1.7.0：可在 Python 3.14 环境安装，`MemoryRoot` 为两个项目生成不同的知识目录。此检查不证明检索隔离或权限控制。
- EverOS 自托管 API 文档说明服务本身不提供认证，而且启动需要抽取模型；其知识检索还需要配置 embedding 和 reranker。当前没有配置模型、向量或重排服务，因此尚未验证真实导入、搜索与 Pi 联动。
- EverOS 文档示例中的 demo 默认使用云端服务，因此此试验未运行该 demo，也未上传任何内容。

## 复现

```sh
npm install --prefix spikes/pi-everos --ignore-scripts
node spikes/pi-everos/pi-check.mjs
uv venv /tmp/hmw-everos-env
uv pip install --python /tmp/hmw-everos-env/bin/python everos==1.7.0
/tmp/hmw-everos-env/bin/python spikes/pi-everos/everos-check.py
```

后续如决定接入 EverOS，须重新评估模型环境和部署，并验证项目权限、撤权、来源、保留策略及个人状态写入隔离。不要把这两个探针当成这些测试已经通过。
