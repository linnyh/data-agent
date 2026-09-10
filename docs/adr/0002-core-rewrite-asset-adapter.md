# 核心域接口化重写，算法资产原样保留经 Adapter 接入

重构边界一刀切在"核心域 vs 算法资产"：数据源注册、工具、Agent、执行管线等核心域按 SOLID 接口化重写（依赖倒置、单一职责）；视频/doc 链的算法模块（fanout_struct、slide_coarse_det、audio_transcribe 等）原样收编在 `src/data_agent/assets/`，通过 Adapter 实现核心域接口接入，内部一个字符不改。

**Considered Options**: 全量重写（改动全部 1.7 万行，风险大：fanout_struct 单文件 1667 行且一半是经长期调优的 prompt 模板，重写会引入不可测的行为漂移）；最小改动（旧代码直接 import，SOLID 约束只约束新代码——会让旧代码的全局状态、路径写死等历史问题继续污染核心域）。

**Consequences**: 依赖方向单向（application → domain ← adapters）；旧代码作为不可变资产，回归验证时新旧实现可逐行为对比。
