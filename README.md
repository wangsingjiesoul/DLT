# 大乐透历史数据、候选推荐与通知服务

本项目只处理中国体育彩票超级大乐透，包含：

- 2007 年至今的历史开奖号码；
- 增量获取最新开奖结果；
- 均衡、趋势、探索三类统计候选；
- 响应式网页和只读 JSON API；
- 默认通过免费的 Qmsg酱向 QQ 发送候选号码；
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
