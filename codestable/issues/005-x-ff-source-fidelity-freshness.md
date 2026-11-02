---
kind: issue
title: "保真抓取源码，并读取最新信息的原页证据"
type: ff
status: closed
created: 2026-11-02
---

# 保真抓取源码，并读取最新信息的原页证据

原始 C 头文件经过网页抽取后丢失尖括号、标识符被转义；最新版本搜索只返回旧摘要，research 又可能把导航而非事实放进结果。现在源码走原文传输并保留可逆编码与 SHA-256，时效问题保持 Jev 自动发现，再限量读页核验并展示相关证据。

- 改动：`src/smart_search/direct_fetch.py`、`src/smart_search/freshness.py` 及 service、Jev、CLI 接入；保留代理和 TLS 主机校验，支持原编码文件导出；同步双语说明与 Skill 镜像。
- 验证：最终全量 pytest 1352 passed、19 skipped；源码 regression 578 passed；mock smoke 24/24；npm launcher、skill parity、pack dry-run、diff 和 arch-check 通过。新增保真/代理、CLI 导出、时效读页与报告回归测试。
- 实测：原六题和 standard research 原文复测。CPython 头文件 3960 bytes 逐字节一致；Python 最新版普通检索及 research 报告均读到正式发布页的版本、日期和链接。首轮代理超时、research 导航摘要失败均保留，修正代码后重新跑完整组题。时效读页增加延迟；论文检索仍有两篇首次提交日期缺失，不属于本次修复的验收成功。
- codestable：已同步 `codestable/spec/retrieval-integrity.md`。
- 取舍：实测暴露的集成问题记录在 [决定笔记](../../.agents/notes/implemented/fix/2026-11-02-source-fidelity-and-freshness.md)。
