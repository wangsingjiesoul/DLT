# AI 连通性检查实施计划

## 目标

新增 `python main.py ai-check` 命令，用一条不会发送通知的命令确认 AI 配置、HTTP 连通性、模型响应 JSON 和候选 ID 约束均正常。

## 设计

- 复用已有 `OpenAICompatibleSelector` 和 `generate_prediction`，不另写一套测试协议。
- 即使 `.env` 中配置了 `AI_REQUIRED=false`，检查命令也强制使用严格模式；任何回退都视为检查失败。
- 只输出模型名、目标期号、状态、候选数量和安全提示，不输出 API Key、Base URL、请求头或完整模型响应。
- 检查命令不构造通知器、不更新开奖数据、不写通知去重状态。
- 提供 `--candidates` 参数，默认用较小候选量完成连通测试，减少本地计算时间；发送给 AI 的候选上限仍由预测器控制。

## 实施步骤

1. 在 `main.py` 增加 `command_ai_check` 和 `ai-check` 子命令。
2. 在 AI 未启用时返回明确、安全的配置错误。
3. 添加成功、未启用、模型失败且不发送通知的测试。
4. 更新 README、零基础指南和 `.env.example` 注释。
5. 运行完整测试、编译检查和 Compose 配置检查。

## 完成标准

- 配置正确时命令退出码为 0，并显示 `used=true`、`status=selected`。
- AI 未启用、超时或返回非法结果时命令退出码为 1。
- 任何情况下都不会调用 QQ/微信通知器，也不会泄漏凭证。
