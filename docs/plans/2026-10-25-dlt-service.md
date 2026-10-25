# 大乐透自动推荐与通知服务 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将现有历史数据项目扩展为可更新、可生成候选、可网页查看、可定时向微信或 QQ 发送消息的部署服务。

**Architecture:** 使用 Python 3.11 标准库实现单体服务；历史数据与运行状态分别保存在 `data/` 和 `runtime/`。通知通过 PushPlus 适配器发送，Web 层只暴露只读接口，自动化周期由内置调度器或 CLI 触发。

**Tech Stack:** Python 3.11、HTML/CSS/JavaScript、Docker、PushPlus HTTP API、`unittest`

---

### Task 1: 数据层和增量更新

**Files:**
- Create: `dlt/data.py`
- Create: `dlt/updater.py`
- Test: `tests/test_data.py`
- Test: `tests/test_updater.py`

**Steps:**
1. 先编写数据校验、原子写入和 HTML 解析测试。
2. 运行测试并确认因模块不存在而失败。
3. 实现最小数据层和更新器。
4. 运行相关测试，预期全部通过。

### Task 2: 候选生成器

**Files:**
- Create: `dlt/predictor.py`
- Test: `tests/test_predictor.py`

**Steps:**
1. 测试同一期号结果可复现、三组号码合法且低重合。
2. 实现滚动频率、遗漏、指数衰减和结构评分。
3. 使用固定种子加权抽样，输出均衡、趋势、探索三类组合。
4. 运行测试，并用完整历史数据生成实际结果。

### Task 3: 通知和自动化周期

**Files:**
- Create: `dlt/notifier.py`
- Create: `dlt/automation.py`
- Test: `tests/test_notifier.py`
- Test: `tests/test_automation.py`

**Steps:**
1. 测试 PushPlus 请求体、缺少 Token、失败重试状态和按期号去重。
2. 实现 Markdown 消息、通知适配器和运行状态文件。
3. 实现“更新 → 预测 → 保存 → 通知”周期。
4. 运行相关测试。

### Task 4: Web 页面和只读 API

**Files:**
- Create: `dlt/web.py`
- Create: `web/index.html`
- Create: `web/styles.css`
- Create: `web/app.js`
- Test: `tests/test_web.py`

**Steps:**
1. 测试健康检查、预测 API、安全头和路径穿越保护。
2. 实现标准库 HTTP 服务。
3. 将已确认的 SVG 视觉转换成响应式页面。
4. 启动本地服务并在浏览器中检查桌面布局。

### Task 5: CLI、调度与部署

**Files:**
- Modify: `main.py`
- Create: `.env.example`
- Create: `Dockerfile`
- Create: `compose.yaml`
- Modify: `.gitignore`
- Modify: `README.md`

**Steps:**
1. 增加 `update`、`predict`、`notify`、`run-once`、`serve` 命令。
2. 增加北京时间开奖日定时执行和重复触发保护。
3. 编写 Docker 与环境变量配置。
4. 更新部署、PushPlus 微信/QQ 接入和排障文档。
5. 运行全部测试、真实更新 dry-run、通知 dry-run 和容器配置检查。

