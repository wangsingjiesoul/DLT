# 大乐透历史数据、候选推荐与通知服务

本项目只处理中国体育彩票超级大乐透，包含：

- 2007 年至今的历史开奖号码；
- 增量获取最新开奖结果；
- 均衡、趋势、探索三类统计候选；
- 通过 OpenAI-compatible 接口调用真实 AI 模型，对合法候选池进行受控重排；
- 响应式网页和只读 JSON API；
- 默认通过免费的 Qmsg酱向 QQ 发送候选号码；
- 内置定时任务和 Docker 部署。

> 开奖结果具有独立随机性。统计评分和 AI 重排都只用于候选排序，不等同于中奖概率，也不承诺提高收益。

零基础读者建议先看 [零基础学习与部署指南](docs/beginner-guide.md)，再回到本页复制命令操作。

## 数据

开奖记录保存在 `data/dlt.json`，按日期从新到旧排列：

```json
{
  "issue": "26121",
  "date": "2026-10-24",
  "front": ["06", "08", "19", "24", "25"],
  "back": ["01", "08"]
}
```

初始数据来自 `Justdoitfor/my-lottery-data` 的 `data/dlt.json`；自动更新器使用 500 历史开奖表格，并在写入前执行期号、日期、号码范围、数量和重复校验。

## 本地运行

项目只使用 Python 3.11 标准库，无需安装第三方依赖。

```bash
python main.py predict
python main.py notify --dry-run
python main.py serve --host 127.0.0.1 --port 8000 --no-scheduler
```

打开 `http://127.0.0.1:8000` 查看页面。

其他命令：

```bash
# 增量更新开奖结果
python main.py update

# 更新、预测并保存，不发送通知
python main.py run-once

# 更新、预测并发送通知
python main.py run-once --notify

# 强制重复发送当前目标期号
python main.py run-once --notify --force-notify

# 严格按时间顺序比较模型与随机基线
python main.py backtest --rounds 30 --candidates 250

# 运行测试
python -m unittest discover -s tests -v

# 配置 AI 后，真实调用一次模型进行安全检查；不会发送通知
python main.py ai-check
```

## 接入真实 AI 模型

项目支持 OpenAI `chat/completions` 形式的兼容接口。模型不会直接生成号码，完整流程是：

```text
历史开奖 → 本地统计特征 → 每种策略的合法候选池
         → 外部 AI 只选择候选 ID → 本地二次校验 → 三组最终候选
```

先把 `.env.example` 复制为 `.env`，再填写你实际购买或部署的模型服务信息：

```dotenv
AI_ENABLED=true
AI_REQUIRED=true
AI_BASE_URL=https://你的服务商地址/v1
AI_API_KEY=你的API密钥
AI_MODEL=服务商提供的准确模型ID
AI_TIMEOUT=30
```

- `AI_REQUIRED=true`：AI 请求、JSON 解析或候选校验失败时，本次预测直接失败，定时任务不会发送号码。
- `AI_REQUIRED=false`：失败时回退为原统计排序，网页、API 和消息中会明确标记“已回退”。
- 不要把 API Key 发到聊天、写进源码或提交到 Git；`.env` 和 Docker 构建上下文都已排除真实密钥。
- Base URL 填到兼容 API 的版本根路径即可，例如末尾为 `/v1`；程序会自动追加 `/chat/completions`。

验证是否真的调用了 AI，并确认返回值能通过候选 ID 校验：

```bash
python main.py ai-check
```

成功输出应包含：

```json
{
  "ok": true,
  "used": true,
  "status": "selected",
  "recommendation_count": 3
}
```

这个命令不更新开奖数据、不保存预测，也不发送 QQ/微信消息；它会强制使用严格模式，任何统计回退都会被判定为检查失败。

`predict`、`notify`、`run-once`、定时任务和 Web 首次生成预测时都会使用同一套 AI 配置。`backtest` 默认只测试确定性的统计层，避免产生大量 API 费用和不可复现结果。

Web 会把同一期、同一模型配置的结果缓存到 `runtime/latest_prediction.json`。多个并发页面请求只会触发一次生成；如果非严格模式发生统计回退，网页会在 10 分钟冷却期后自动重新尝试 AI，避免每次刷新都重复消耗接口额度。

## QQ 通知接入（默认、免费）

项目默认使用 Qmsg酱发送 QQ 消息，不要求购买会员或付费实名认证。PushPlus 适配器仍保留，但不再作为默认方案。

### 1. 获取 Qmsg Key

1. 使用 QQ 登录 Qmsg酱控制台。
2. 复制控制台生成的 Key。
3. 添加控制台提供的 Qmsg酱机器人为 QQ 好友。
4. 向机器人发送一次自己的 Key，完成单聊绑定。

### 2. 配置环境变量

复制示例文件：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
NOTIFY_PROVIDER=qmsg
QMSG_KEY=你的QmsgKey
QMSG_GROUP=
```

- 发到绑定的 QQ 单聊：让 `QMSG_GROUP` 保持为空。
- 发到 QQ 群：完成 Qmsg 群绑定后，将群号填入 `QMSG_GROUP`。
- `QMSG_KEY` 是通知凭证，不要发给别人。

`.env` 已被 Git 忽略，真实 Key 不会进入仓库。

如已有可用的 PushPlus 配置，可将 `NOTIFY_PROVIDER` 改为 `pushplus`，并继续使用 `PUSHPLUS_*` 环境变量。

PushPlus 微信和 QQ 同时发送：

```dotenv
NOTIFY_PROVIDER=pushplus
PUSHPLUS_TOKEN=你的Token
PUSHPLUS_CHANNELS=wechat,qq
```

每个渠道按“目标期号 + 渠道”单独记录成功状态。微信成功、QQ 失败时，调度器只重试 QQ，不会向微信重复发送。

### 3. 测试消息

先查看消息内容：

```bash
python main.py notify --dry-run
```

确认后发送：

```bash
python main.py notify
```

## Docker 部署

```bash
docker compose up -d --build
docker compose logs -f dlt
```

网页端口由 `.env` 中的 `DLT_PORT` 决定；默认地址为 `http://服务器IP:8000`。

定时任务默认开启，因此 Docker 部署必须配置所选渠道的凭证；默认是 `QMSG_KEY`。如果只想看网页、不发送消息，可在本地使用 `python main.py serve --no-scheduler`。

默认自动任务：北京时间每周一、周三、周六 `22:35` 执行。

```dotenv
DLT_TIMEZONE=Asia/Shanghai
DLT_SCHEDULE_TIME=22:35
DLT_SCHEDULE_WEEKDAYS=0,2,5
DLT_SCHEDULE_RETRY_MINUTES=10
```

执行顺序：

```text
获取最新开奖 → 校验并合并 → 生成统计候选池 → AI 重排并本地复核
             → 保存结果 → 按“期号 + 渠道”去重发送
```

如果开奖页在 `22:35` 尚未更新到当天数据，调度器不会发送旧预测，会默认每 10 分钟重试。如果数据源或通知接口失败，程序不会覆盖原有开奖数据，也不会把失败期号标记为已发送。

## API

- `GET /api/health`：服务和数据状态
- `GET /api/prediction`：当前候选结果
- `GET /api/history/latest?limit=10`：最近开奖记录，最多 100 条

Web 服务不提供远程更新或发送接口，避免公网部署后被他人滥用。更新和通知只能通过定时任务或服务器上的 CLI 触发。

## 目录结构

```text
dlt/        核心 Python 模块
data/       历史开奖号码
runtime/    运行时预测和通知状态（不提交 Git）
tests/      自动化测试
web/        网页资源
docs/       架构、ADR 和实施计划
```

关键文件：

- `dlt/data.py`：读取、校验和安全写入开奖 JSON。
- `dlt/predictor.py`：统计特征、候选池、AI 选择约束和最终评分。
- `dlt/ai.py`：OpenAI-compatible 请求、超时、响应限制和 JSON 解析。
- `dlt/automation.py`：更新、预测、保存、通知去重和失败重试。
- `main.py`：环境变量、命令行、定时器和各模块装配。

## 学习顺序

1. [零基础学习与部署指南](docs/beginner-guide.md)：先跑起来，再理解数据流。
2. `dlt/data.py`：学习 JSON、函数、异常和数据校验。
3. `dlt/predictor.py`：学习特征、随机种子、候选池和 AI 约束。
4. `dlt/ai.py`：学习 HTTP 请求、JSON 响应和密钥保护。
5. `dlt/automation.py` 与 `main.py`：学习如何把独立模块串成可部署服务。
6. `tests/`：从测试名称反向理解每项功能为什么存在。
