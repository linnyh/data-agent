# 会话历史以 checkpoint 状态为事实源，不建独立消息表

前端需要会话历史回放（切换会话后显示历次问答），但**不新建消息表**：历史由 LangGraph checkpoint 的 state history 提供（`GET /sessions/{id}/history` 读每轮 goal + outcome），checkpoint 是唯一事实源。

**Considered Options**: 独立消息表（聊天产品的常规做法——但会引入双写：图状态与消息表要保持一致，追问/interrupt 的中间态同步复杂；且 checkpoint 已按 thread 完整记录了每轮状态，消息表是冗余副本）；前端 localStorage 存历史（换浏览器即丢，且与后端状态漂移）。

**Consequences**: 历史粒度 = "每轮分析"（goal + outcome），而非每条原始消息——足够回放问答，但不记录流式中间过程；换 Postgres 时 checkpoint 历史随 saver 迁移，无额外数据迁移。若未来需要"逐 token 消息审计"，届时再引入消息表并从 checkpoint 回填。
