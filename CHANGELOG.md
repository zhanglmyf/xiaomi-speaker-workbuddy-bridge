# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## Unreleased

- 搭建仓库维护体系：贡献指南、issue 模板、自检脚本、更新日志。

## 1.1.0

- **脱敏通用化**：移除所有宿主环境的私人信息（IP、代理、姓名、路径），改为占位符或动态发现。
- **网络自适应**：`netprobe.py` 自动发现 xiaomusic 地址、桥接可达地址、docker 通道；网关地址改为运行时动态获取，不再写死。
- **单一配置源**：口令、端口、助手名、xiaomusic 端口全部在 `config.json`，`deploy.py` 部署时全链路同步。
- **跨平台文档**：新增面向 DSH / Claude / Codex / 通义千问等任意 Agent 的使用说明。

## 1.0.0

- 首个公开版本：小爱音箱经 xiaomusic 接入 WorkBuddy，语音下任务、音箱播报反馈。
- 支持任意音箱型号、任意 OpenAI 兼容 LLM、方言理解表、语音任务单。
- 两个根治补丁：token 反复被删、对话捕获 400。
