# Decision 004 — Chat-first 本地数据 Agent

## 背景

早期控制面把每个用户回合强制建模为 Task/Run，并要求 Agent 返回固定 JSON，导致闲聊、澄清和真正的长时分析共享了一套过重的生命周期。门控逻辑也容易变成写死的意图路由器，限制了通用 Agent 的工具选择能力。

## 决策

1. `Conversation` 是默认用户入口。普通消息调用一个 Agent loop，模型最终输出是自然语言；PydanticAI 只为工具参数和工具调用边界提供类型化协议。
2. 可见的 user/assistant 消息使用独立 `conversation_messages` 表，可按请求选择持久化；不保存隐藏思考、原始模型请求、工具载荷或 PII 映射。
3. `Task/Run` 改名为显式 `Analysis Job` 的实现事实：只有 Python/SQL、图表发布、Wolfram 计算、长时间运行或需要恢复/取消时才创建。
4. Sandbox、Finding Verification 和正式资源发布仍保留，但它们是分析作业和产物的边界，不是每条聊天消息的门控。验证针对 Finding/Artifact 等可发布事实，而不是针对普通自然语言回复套一个合规 JSON schema。
5. 本地 Agent 先使用 ProjectCorpus 的文件列表、FTS 检索和有界文件读取工具；复杂计算按需升级到显式分析作业。Skill Registry 通过内容 hash 动态发现本地 Skill，Wolfram Skill 优先 MCP，缺省时只允许批准的 Sandbox 使用 `wolframscript`。

## 隐私边界

所有会到达云模型的消息、工具结果和异常仍经过 `ModelGateway`。secret fail-closed；PII 只在对应 Conversation scope 的 Privacy SQLite 中建立占位映射。保存本地对话并不等于把内容发到云端，且没有“为了验证合规”而回显原始请求的旁路。

## 迁移策略

旧 `/projects/{project_id}/tasks` API、Worker 和 Task 页面继续作为显式分析作业兼容入口。新 WebUI 默认进入 Conversation；后续可把“开始分析”按钮接到一个明确的 Analysis Job 创建动作，再逐步收敛旧页面，而不需要把稳定的 Sandbox/发布事实模型删除重写。
