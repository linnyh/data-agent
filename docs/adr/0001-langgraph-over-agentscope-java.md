# 用 LangGraph（Python）而非 AgentScope Java 2.0 重构

重构框架选 LangGraph，弃用最初考虑的 AgentScope Java 2.0。原因是现有方案的三条数据链（结构化数据、文档抽取、视频多模态）是经过实测验证的 Python 算法资产（约 6000 行：fanout_struct 抽取引擎、faster-whisper ASR、码率突变幻灯片检测），Java 生态无成熟等价物，重写风险与成本远高于收益；留在 Python 则这些模块可直接复用。

**Considered Options**: AgentScope Java 2.0（有官方 dataagent 示例与窄工具同源设计，但要求重写全部算法资产且多 Agent 编排需自建）；LangGraph（图编排、checkpoint、interrupt/resume 恰好匹配对话式 Agent 的会话状态需求，代价是接受 langchain-core 硬依赖与消息模型）。

**Consequences**: 视频/doc 链作为"算法资产"以 Adapter 方式原样保留（见 ADR-0002）；不采用 LangGraph Platform 托管（licensed 镜像），自建 FastAPI 服务。
