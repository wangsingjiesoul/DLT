# 大乐透项目零基础学习与部署指南

这份文档假设你刚开始学习 Python、Git、API 和 Docker。目标不是一次记住所有代码，而是先把项目跑起来，再沿着一条清晰的数据流理解它。

> 重要：彩票开奖是独立随机事件。这个项目用于学习数据处理、自动化、Web 和 AI API 接入。统计评分或 AI 选择都不等同于中奖概率，也不能保证收益。

## 1. 这个项目做了什么

一次完整自动任务包含六步：

```text
1. 获取新开奖
   ↓
2. 校验并合并 data/dlt.json
   ↓
3. 用历史数据生成合法候选池
   ↓
4. 可选：调用外部 AI，只让它选择候选 ID
   ↓
5. 再次校验并保存 runtime/latest_prediction.json
   ↓
6. 通过 QQ 或微信发送，并记录已成功的渠道
```

为什么不让 AI 直接写号码？因为大语言模型可能返回少号码、多号码、重复号码、越界号码或不合法文本。当前设计先由本地代码生成合法候选，再让 AI 选择候选 ID，最终结果更容易验证和测试。

## 2. 先认识几个词

- **Python**：本项目的后端编程语言。
- **JSON**：用键和值保存结构化数据的文本格式。
- **API**：两个程序通过 HTTP 交换数据的接口。
- **环境变量**：运行时配置，例如 API Key；不需要写死在源代码里。
- **CLI**：在 PowerShell 或终端里输入的命令行程序。
- **Docker**：把程序和运行环境打包成容器，方便服务器部署。
- **测试**：自动检查代码行为有没有被改坏的小程序。
- **候选池**：本地统计程序生成的一批合法号码组合。
- **重排**：不创造新候选，只改变已有候选的选择或顺序。

## 3. 目录结构

```text
DLT/
├─ data/dlt.json          历史开奖数据
├─ dlt/                   Python 后端模块
│  ├─ data.py             数据读取、校验、原子写入
│  ├─ updater.py          获取并合并新开奖
│  ├─ predictor.py        统计特征、候选池和 AI 选择校验
│  ├─ ai.py               外部 AI HTTP 客户端
│  ├─ backtest.py         严格按时间顺序回测
│  ├─ notifier.py         QQ / 微信消息
│  ├─ automation.py       把更新、预测、通知串起来
│  └─ web.py              网页和只读 API
├─ web/                   HTML、CSS、JavaScript 页面
├─ tests/                 自动化测试
├─ docs/                  架构、决策和学习文档
├─ main.py                命令行和定时器入口
├─ .env.example           配置模板，不含真实密钥
├─ compose.yaml           Docker Compose 配置
└─ Dockerfile             镜像构建步骤
```

`runtime/` 会在程序运行后出现，里面是最近预测和通知去重状态。它不提交到 Git。

## 4. 第一次在 Windows 本地运行

### 4.1 安装并确认 Python

安装 Python 3.11 或更高的兼容版本，然后在 PowerShell 输入：

```powershell
python --version
```

看到类似 `Python 3.11.x` 即可。

### 4.2 进入项目目录

```powershell
Set-Location D:\stuady\Project\DLT
```

### 4.3 先运行测试

```powershell
python -m unittest discover -s tests -v
```

最后看到 `OK` 表示基础功能通过。测试不会调用真实 AI，也不会真的发送消息。

### 4.4 不配置 AI，先看统计预测

```powershell
python main.py predict
```

输出中会有：

```json
"ai": {
  "enabled": false,
  "used": false,
  "status": "disabled"
}
```

这表示程序正常，但当前只使用本地统计层。

### 4.5 启动网页

```powershell
python main.py serve --host 127.0.0.1 --port 8000 --no-scheduler
```

浏览器打开 `http://127.0.0.1:8000`。按 `Ctrl+C` 停止服务。

`--no-scheduler` 的意思是只看网页，不启动自动更新和通知，所以此时不要求配置 QQ 或微信。

## 5. 配置文件 `.env`

先复制模板：

```powershell
Copy-Item .env.example .env
```

`.env` 是本机或服务器私有配置。项目的 `.gitignore` 和 `.dockerignore` 都会排除它，但你仍然不应该把文件内容发给别人。

格式非常简单：

```dotenv
配置名=配置值
```

等号左右建议不要添加多余空格。以 `#` 开头的是说明文字。

## 6. 接入 AI 模型

### 6.1 你需要准备什么

向模型服务商确认三个值：

1. OpenAI-compatible Base URL；
2. API Key；
3. 准确的模型 ID。

不要根据展示名称猜模型 ID，应该复制服务商控制台或文档给出的值。

### 6.2 编辑 `.env`

```dotenv
AI_ENABLED=true
AI_REQUIRED=true
AI_BASE_URL=https://你的服务商地址/v1
AI_API_KEY=你的真实密钥
AI_MODEL=服务商提供的准确模型ID
AI_TIMEOUT=30
```

含义：

- `AI_ENABLED`：是否调用外部模型。
- `AI_REQUIRED`：是否强制本次结果必须经过 AI。
- `AI_BASE_URL`：兼容接口根地址；程序自动追加 `/chat/completions`。
- `AI_API_KEY`：调用凭证，只保存在环境变量和进程内存中。
- `AI_MODEL`：请求体里的模型 ID。
- `AI_TIMEOUT`：最多等待多少秒，可填写 1～120。

推荐先使用 `AI_REQUIRED=true`。这样如果服务商地址、Key 或模型名配错，程序会明确失败，也不会把统计回退结果当成 AI 结果发送。

### 6.3 验证真实调用

```powershell
python main.py ai-check
```

`ai-check` 不更新开奖、不保存预测，也不会发送 QQ/微信。它只进行一次严格的真实模型调用。成功时应该看到：

```json
{
  "ok": true,
  "used": true,
  "model": "你的模型ID",
  "status": "selected",
  "recommendation_count": 3
}
```

每组推荐还会有 `selection_reason`，表示模型为什么从该策略的候选中选它。

如果 `used` 不是 `true`：

- 检查 Base URL 是否真的兼容 `chat/completions`；
- 检查 Key 是否有效并有余额或额度；
- 检查模型 ID 是否准确；
- 检查服务器能否访问模型服务；
- 查看终端或 `docker compose logs -f dlt` 的安全错误信息。

程序不会把 Authorization 请求头、API Key 或完整错误响应写入错误消息。

## 7. AI 在代码里如何工作

### 7.1 本地统计层

`dlt/predictor.py` 先计算：

- 最近 30、100、300 期频率；
- 越新的开奖权重越高的指数衰减热度；
- 每个号码已经遗漏多少期；
- 分区、奇偶、和值、跨度、连号和后区间距；
- 三组结果之间的号码重合度。

然后分别为均衡、趋势、探索策略生成候选池。固定随机种子保证相同期号、相同代码版本能复现相同候选池。

### 7.2 发送给 AI 的内容

每个候选都有类似这样的摘要：

```json
{
  "candidate_id": "balanced-12位摘要",
  "strategy": "balanced",
  "rank": 1,
  "front": ["03", "10", "21", "26", "33"],
  "back": ["01", "07"],
  "statistical_score": 78.5,
  "factors": {
    "trend": 65,
    "gap": 48,
    "structure": 92
  }
}
```

AI 被要求只返回三个候选 ID 和理由。

### 7.3 本地安全复核

模型返回后，程序逐项检查：

- 顶层是不是 JSON 对象；
- 是否正好有三个选择；
- 是否恰好包含 balanced、trend、explore；
- ID 是否真的存在于本次候选池；
- ID 是否属于声明的策略；
- 三组号码是否重复；
- 理由是否为非空短文本。

只有全部通过，`ai.used` 才会变成 `true`。

## 8. 严格模式和回退模式

### 严格模式

```dotenv
AI_REQUIRED=true
```

适合你明确要求“发送的号码必须经过 AI”时使用。AI 超时、欠费、返回非法 JSON 或非法 ID，预测会失败，通知不会发送，原来的历史数据和已保存预测不会被破坏。

### 回退模式

```dotenv
AI_REQUIRED=false
```

适合更重视服务连续性的情况。AI 失败后仍会生成统计结果，但以下位置会明确写出回退：

- 命令行输出的 `ai.status`；
- Web 页面 AI 状态；
- QQ 或微信消息。

## 9. 通知配置

### 9.1 QQ：Qmsg酱

```dotenv
NOTIFY_PROVIDER=qmsg
QMSG_KEY=你的QmsgKey
QMSG_GROUP=
```

`QMSG_GROUP` 留空表示发给绑定的 QQ 单聊；填写已绑定群号则发群消息。

### 9.2 微信和 QQ：PushPlus

```dotenv
NOTIFY_PROVIDER=pushplus
PUSHPLUS_TOKEN=你的Token
PUSHPLUS_CHANNELS=wechat,qq
```

先预览消息：

```powershell
python main.py notify --dry-run
```

实际发送：

```powershell
python main.py notify
```

自动任务会按“目标期号 + 渠道”记录成功状态。假设微信成功而 QQ 失败，下次只补发 QQ。

## 10. 常用命令表

| 命令 | 作用 | 是否调用 AI |
|---|---|---|
| `python main.py update` | 只更新开奖数据 | 否 |
| `python main.py predict` | 生成并保存预测 | 按配置 |
| `python main.py notify --dry-run` | 预览消息 | 按配置 |
| `python main.py notify` | 立即发送消息 | 按配置 |
| `python main.py run-once` | 更新并预测，不通知 | 按配置 |
| `python main.py run-once --notify` | 更新、预测、通知 | 按配置 |
| `python main.py backtest` | 滚动回测统计层 | 否 |
| `python main.py ai-check` | 严格验证一次真实 AI 调用，不发送通知 | 是 |
| `python main.py serve --no-scheduler` | 只启动网页 | 缓存失效时按配置 |
| `python main.py serve` | 网页 + 定时任务 | 按配置 |

## 11. Web 和 API

网页本身没有把密钥传给浏览器。浏览器只读取后端已经生成的结果。

- `/`：可视化页面；
- `/api/health`：服务和历史数据状态；
- `/api/prediction`：当前预测 JSON；
- `/api/history/latest?limit=10`：最近 10 条开奖。

Web 只开放 GET 和 HEAD，不提供远程“立即发送”或“更新数据”按钮，避免公网部署后被其他人滥用你的模型额度和通知额度。

Web 使用进程内锁保护预测缓存：首次有多个浏览器同时访问时，只允许一个请求调用模型，其他请求等待并复用结果。如果回退模式遇到 AI 故障，回退结果会缓存 10 分钟，然后页面再尝试一次 AI；这样既能自动恢复，又不会因反复刷新快速消耗额度。

## 12. 定时任务

默认配置：

```dotenv
DLT_TIMEZONE=Asia/Shanghai
DLT_SCHEDULE_TIME=22:35
DLT_SCHEDULE_WEEKDAYS=0,2,5
DLT_SCHEDULE_RETRY_MINUTES=10
```

Python 里周一是 0、周三是 2、周六是 5。到点后程序先检查当天开奖是否已经出现在数据源中；没有更新就每 10 分钟重试，不会拿旧数据发送下一期号码。

## 13. Docker 部署

服务器安装 Docker 和 Docker Compose 后，在项目目录执行：

```bash
docker compose up -d --build
docker compose logs -f dlt
```

检查状态：

```bash
docker compose ps
```

更新代码后的常见流程：

```bash
git pull
docker compose up -d --build
docker compose logs -f dlt
```

数据和 `runtime/` 使用 Docker 命名卷保存，所以普通的重新构建不会清空它们。不要随意执行删除卷的命令。

## 14. 如何阅读测试

测试文件名和源代码模块基本对应：

- `test_data.py`：不合法号码必须被拒绝；
- `test_updater.py`：历史 HTML 能正确解析和去重；
- `test_predictor.py`：预测可复现，AI 不能越过候选池；
- `test_ai.py`：HTTP 请求和 JSON 响应安全；
- `test_automation.py`：重复消息和部分渠道失败；
- `test_web.py`：路径穿越、响应头和缓存隔离；
- `test_notifier.py`：消息内容和凭证不泄漏。

改代码后先运行全部测试。如果某项失败，测试名称通常已经说明被破坏的行为。

## 15. 建议的学习练习

按从简单到困难的顺序：

1. 修改网页标题和主题颜色；
2. 给 `/api/health` 增加模型是否启用的非敏感状态；
3. 给预测结果增加前区和值展示；
4. 为新增字段编写测试；
5. 新增一个通知适配器，但继续保持密钥来自环境变量；
6. 新增另一种统计策略，并在回测里与随机基线比较。

每次只改一件事，先写或补测试，再运行完整测试。这样最容易知道错误来自哪里。

## 16. 常见问题

### 为什么配置了 AI，页面仍显示未启用？

确认启动进程之前已经保存 `.env`，并重启 Python 进程或 Docker 容器。环境变量只在进程启动时读取。

### 为什么 AI 返回成功，但程序拒绝结果？

HTTP 成功不代表业务格式合法。模型必须正好返回三个策略、合法候选 ID 和非空理由。本地校验失败是预期的安全保护。

### 为什么不让 AI 读取全部几千期原始数据？

把全部原始记录放进提示词会增加费用、延迟和上下文噪声。当前设计先用确定性代码压缩成统计特征和少量候选，再让 AI 做它更适合的比较与解释。

### AI 能提高中奖概率吗？

没有可靠证据说明这种重排能改变独立随机开奖的理论概率。项目应被看作软件工程和数据分析练习，而不是收益工具。

### 历史数据源可靠吗？

当前自动更新使用第三方历史页面，不是官方 API。程序会做格式、范围、数量、日期、重复和排序校验，但第三方源仍可能延迟或停止服务。重要使用场景应增加第二数据源交叉核验。
