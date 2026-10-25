# 大乐透历史数据、候选推荐与通知服务

本项目只处理中国体育彩票超级大乐透，包含：

- 2007 年至今的历史开奖号码；
- 增量获取最新开奖结果；
- 均衡、趋势、探索三类统计候选；
- 响应式网页和只读 JSON API；
- 通过 PushPlus 向微信或 QQ 发送候选号码；
- 内置定时任务和 Docker 部署。

> 开奖结果具有独立随机性。候选评分只用于历史统计和组合排序，不等同于中奖概率，也不承诺提高收益。

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
```

## 微信或 QQ 接入

本项目默认使用 PushPlus。推荐优先使用微信：绑定公众号后只需要 Token，配置最少；QQ 也支持，但发送到 QQ 群时还需要渠道配置编码。

### 1. 获取 Token

1. 登录 PushPlus。
2. 关注其微信公众号并完成账号绑定。
3. 在“发送消息”页面复制个人 Token。
4. 如使用 QQ，在 PushPlus 渠道配置中完成 QQ 机器人或好友配置。

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
PUSHPLUS_TOKEN=你的真实Token
PUSHPLUS_CHANNEL=wechat
```

- 发到微信：`PUSHPLUS_CHANNEL=wechat`
- QQ 默认机器人发给自己：只填 `PUSHPLUS_CHANNEL=qq`，`PUSHPLUS_OPTION` 留空
- QQ 群或指定机器人：填写 `PUSHPLUS_OPTION`（PushPlus 的渠道配置编码），并让 `PUSHPLUS_TOPIC`、`PUSHPLUS_TO` 保持为空
- 好友消息使用 `PUSHPLUS_TO` 填好友令牌；它不是 QQ 号
- 使用 PushPlus 一对多群组功能时填写 `PUSHPLUS_TOPIC`

`.env` 已被 Git 忽略，真实 Token 不会进入仓库。

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

网页地址为 `http://服务器IP:8000`。

定时任务默认开启，因此 Docker 部署必须配置 `PUSHPLUS_TOKEN`；如果只想看网页、不发送消息，可在本地使用 `python main.py serve --no-scheduler`。

默认自动任务：北京时间每周一、周三、周六 `22:35` 执行。

```dotenv
DLT_TIMEZONE=Asia/Shanghai
DLT_SCHEDULE_TIME=22:35
DLT_SCHEDULE_WEEKDAYS=0,2,5
DLT_SCHEDULE_RETRY_MINUTES=10
```

执行顺序：

```text
获取最新开奖 → 校验并合并 → 生成下一期候选 → 保存结果 → 按期号去重发送
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
