# Capabilities

- Capability 是面向 agent 的窄接口，不是拥有所有服务的全局对象。
- 只组合领域服务与边界协议；第三方 SDK 留在 Provider。
- V1 能力限于 Project 文件列举/检索/检查、分析执行、Workspace 浏览、产物/血缘、非向量记忆、Coverage 与 Finding 提交。
- 不暴露 Host shell、动态装包、任意网络、外部 API、浏览器、邮件或在线数据库。
- 所有返回模型的内容仍须经过 ModelGateway 的隐私处理。
