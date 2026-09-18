# 关系类资产的研究依据与实现边界

本目录是统一软件知识沉淀方法中的关系表示候选，不要求程序案例、修复规则、状态机与操作技能全部转换成图。方法总览见 [METHODOLOGY.md](../METHODOLOGY.md)。

- [A Knowledge Graph-Based Approach for Assisted PLC Code Generation](https://doi.org/10.1145/3804601.3804649)，CAICE 2026：已有工作将系统结构、控制逻辑模板及其关系用于辅助 PLC 代码生成。因此，“PLC 加知识图谱”本身不能作为本文的贡献。需要进一步比较训练修复证据、条件化知识提炼及递归版本准入等机制；不能在未充分比较前声称这些机制此前不存在。
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)：结构化记忆、关联建立与记忆更新已有相关研究。本轮查阅摘要，不能据此作完整实现等价性判断。
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956)：动态记忆的图表示提供相关设计背景，其实验结论不能直接外推至 PLC 生成。
- [OWL 2 概述](https://www.w3.org/TR/owl2-overview/)与 [SHACL 推荐规范](https://www.w3.org/TR/shacl/)分别支持概念/关系描述和 RDF 数据约束检查。图结构合规不证明 PLC 程序或抽象技能正确。
- [RDFLib](https://rdflib.readthedocs.io/en/stable/)与 [pySHACL](https://github.com/RDFLib/pySHACL)是候选工程工具，采用这些工具本身不构成研究贡献。
- [MPC-Coder: A Dual-Knowledge Enhanced Multi-Agent System with Closed-Loop Verification for PLC Code Generation](https://www.mdpi.com/2073-8994/18/2/248)与本研究直接相关；本轮全文请求受到访问限制，详细机制与比较边界仍待核查。

当前 `plc.ttl` 和 `shapes.ttl` 是尚未执行验证的草案，没有构建或部署训练图谱。本轮依赖安装因本机磁盘不足失败，失败环境已清理。后续若启用此表示，应使用隔离依赖环境验证正例与反例，并记录所用版本；不能把文件存在称为图谱机制已实现或已有效。

进入论文参考文献前，仍须核对正式版本、作者、页码、DOI 与已有 bib key，并由作者人工校对。此文件是研究笔记，没有修改论文或文献数据库。
