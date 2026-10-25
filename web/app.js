// 页面只调用只读 API。所有动态文本都用 textContent 写入，避免把模型文本当成 HTML 执行。
const factorLabels = {
  trend: "近期热度",
  gap: "遗漏修正",
  structure: "结构合理性",
  diversity: "组合差异度",
};

function text(id, value) {
  document.getElementById(id).textContent = value;
}

function ball(number, area) {
  const node = document.createElement("span");
  node.className = `ball ${area}`;
  node.textContent = number;
  return node;
}

// 因子分数来自后端。这里把异常值限制在 0～100，防止进度条溢出。
function renderFactors(factors) {
  const root = document.getElementById("factorList");
  root.replaceChildren();

  Object.entries(factorLabels).forEach(([key, label]) => {
    const value = Math.max(0, Math.min(100, Number(factors[key] || 0)));
    const row = document.createElement("div");
    row.className = "factor";

    const head = document.createElement("div");
    head.className = "factor-head";
    const name = document.createElement("span");
    name.textContent = label;
    const score = document.createElement("code");
    score.textContent = String(value);
    head.append(name, score);

    const track = document.createElement("div");
    track.className = "factor-track";
    const fill = document.createElement("i");
    fill.style.width = `${value}%`;
    track.append(fill);
    row.append(head, track);
    root.append(row);
  });
}

function aiStatusText(ai) {
  if (!ai || !ai.enabled) return "AI 未启用 · 当前为统计排序";
  if (ai.used) return `AI 已参与 · ${ai.model || "已配置模型"}`;
  return "AI 调用失败 · 已回退统计排序";
}

function defaultStrategyNote(item, index) {
  const rank = item.candidate_rank ? `候选池第 ${item.candidate_rank} 名` : "本地候选";
  if (item.selection_reason) return `${rank} · AI：${item.selection_reason}`;
  if (index === 0) return "冷热混合 · 三区覆盖";
  if (index === 1) return "近期频率 · 重复惩罚";
  return "遗漏因子 · 多样化";
}

function renderMethodology(data) {
  const method = data.methodology || {};
  const root = document.getElementById("methodList");
  root.replaceChildren();
  const rows = [
    `历史样本：${Number(method.history_used || data.history_count).toLocaleString("zh-CN")} 期`,
    `统计窗口：${(method.frequency_windows || []).join(" / ") || "未提供"} 期`,
    `结构校准：最近 ${Number(method.structure_sample_size || 0).toLocaleString("zh-CN")} 期经验分布`,
    method.ai_role || "AI 未参与候选选择",
    method.scientific_limit || data.risk_notice,
  ];
  rows.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    root.append(item);
  });
}

function renderPrediction(data) {
  text("targetIssue", data.target_issue);
  text("targetDate", `预计日期 ${data.target_date}`);
  text("modelVersion", `统计模型 ${data.model_version}`);
  text("aiStatus", aiStatusText(data.ai));
  text("syncMeta", `${Number(data.history_count).toLocaleString("zh-CN")} 期 · 最新 ${data.based_on_issue}`);
  text("basedOn", `数据截至 ${data.based_on_issue} / ${data.based_on_date}`);
  text("hotFront", data.signals.hot_front_30.join(" · "));
  text("frontGap", `${data.signals.front_largest_gap.number} / ${data.signals.front_largest_gap.periods}期`);
  text("backGap", `${data.signals.back_largest_gap.number} / ${data.signals.back_largest_gap.periods}期`);
  renderMethodology(data);

  const root = document.getElementById("recommendations");
  root.replaceChildren();
  data.recommendations.forEach((item, index) => {
    // 使用 button 让键盘用户也能切换右侧的因子说明。
    const card = document.createElement("button");
    card.type = "button";
    card.className = `recommendation${index === 0 ? " active" : ""}`;
    card.setAttribute("aria-label", `${item.label}策略候选号码`);

    const copy = document.createElement("div");
    copy.className = "strategy-copy";
    const name = document.createElement("span");
    name.className = "strategy-name";
    name.textContent = `${String.fromCharCode(65 + index)} · ${item.label}策略`;
    const title = document.createElement("strong");
    title.textContent = index === 0 ? "结构优先" : index === 1 ? "近期动量" : "低重合覆盖";
    const note = document.createElement("small");
    note.textContent = defaultStrategyNote(item, index);
    copy.append(name, title, note);

    const balls = document.createElement("div");
    balls.className = "balls";
    item.front.forEach((number) => balls.append(ball(number, "front")));
    const divider = document.createElement("span");
    divider.className = "divider";
    balls.append(divider);
    item.back.forEach((number) => balls.append(ball(number, "back")));

    const score = document.createElement("div");
    score.className = "score";
    const scoreLabel = document.createElement("small");
    scoreLabel.textContent = "综合评分";
    const scoreValue = document.createElement("strong");
    scoreValue.textContent = String(item.score);
    score.append(scoreLabel, scoreValue);

    card.append(copy, balls, score);
    card.addEventListener("click", () => {
      document.querySelectorAll(".recommendation").forEach((node) => node.classList.remove("active"));
      card.classList.add("active");
      renderFactors(item.factors);
    });
    root.append(card);
  });

  if (data.recommendations[0]) renderFactors(data.recommendations[0].factors);
}

function showError(error) {
  const root = document.getElementById("recommendations");
  root.replaceChildren();
  const message = document.createElement("div");
  message.className = "error";
  message.textContent = `读取预测失败：${error.message}`;
  root.append(message);
}

// 历史开奖和预测使用两个独立请求。用户翻页时不会再次调用可能产生费用的 AI。
const historyState = {
  page: 1,
  pageSize: 30,
  totalPages: 1,
  controller: null,
};

function historyBall(number, area) {
  const node = document.createElement("span");
  node.className = `history-ball ${area}`;
  node.textContent = number;
  return node;
}

function renderHistoryRows(items) {
  const root = document.getElementById("historyRows");
  root.replaceChildren();
  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "history-message";
    cell.textContent = "这一页没有开奖记录。";
    row.append(cell);
    root.append(row);
    return;
  }

  items.forEach((draw) => {
    const row = document.createElement("tr");
    const issue = document.createElement("td");
    issue.className = "history-issue";
    issue.textContent = draw.issue;
    const date = document.createElement("td");
    date.className = "history-date";
    date.textContent = draw.date;
    const front = document.createElement("td");
    const frontBalls = document.createElement("div");
    frontBalls.className = "history-balls";
    draw.front.forEach((number) => frontBalls.append(historyBall(number, "front")));
    front.append(frontBalls);
    const back = document.createElement("td");
    const backBalls = document.createElement("div");
    backBalls.className = "history-balls";
    draw.back.forEach((number) => backBalls.append(historyBall(number, "back")));
    back.append(backBalls);
    row.append(issue, date, front, back);
    root.append(row);
  });
}

function setHistoryLoading(loading) {
  document.getElementById("historyTableWrap").setAttribute("aria-busy", String(loading));
  document.getElementById("historyPrevious").disabled = loading || historyState.page <= 1;
  document.getElementById("historyNext").disabled = loading || historyState.page >= historyState.totalPages;
  document.getElementById("historyPageSize").disabled = loading;
}

function updateHistoryControls(data) {
  historyState.page = data.page;
  historyState.pageSize = data.page_size;
  historyState.totalPages = data.total_pages;
  text("historyTotal", `共 ${Number(data.total).toLocaleString("zh-CN")} 期`);
  text("historyPageMeta", `第 ${data.page} / ${data.total_pages} 页`);
  const input = document.getElementById("historyPageInput");
  input.value = String(data.page);
  input.max = String(data.total_pages);
}

async function loadHistory(page = historyState.page) {
  // 快速连续翻页时取消旧请求，避免较慢的旧响应覆盖新页面。
  if (historyState.controller) historyState.controller.abort();
  const controller = new AbortController();
  historyState.controller = controller;
  historyState.page = page;
  setHistoryLoading(true);
  text("historyStatus", "正在加载…");
  try {
    const query = new URLSearchParams({ page: String(page), page_size: String(historyState.pageSize) });
    const response = await fetch(`/api/history?${query}`, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    // 数据量发生变化后，旧页码可能越界；自动回到新的最后一页。
    if (data.page > data.total_pages) {
      await loadHistory(data.total_pages);
      return;
    }
    updateHistoryControls(data);
    renderHistoryRows(data.items);
    text("historyStatus", `已显示第 ${data.page} 页`);
  } catch (error) {
    if (error.name === "AbortError") return;
    const root = document.getElementById("historyRows");
    root.replaceChildren();
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "history-message error-text";
    cell.textContent = `读取历史开奖失败：${error.message}`;
    row.append(cell);
    root.append(row);
    text("historyStatus", "加载失败，请稍后重试");
  } finally {
    if (historyState.controller === controller) setHistoryLoading(false);
  }
}

document.getElementById("historyPrevious").addEventListener("click", () => {
  if (historyState.page > 1) loadHistory(historyState.page - 1);
});

document.getElementById("historyNext").addEventListener("click", () => {
  if (historyState.page < historyState.totalPages) loadHistory(historyState.page + 1);
});

document.getElementById("historyPageSize").addEventListener("change", (event) => {
  historyState.pageSize = Number(event.target.value);
  loadHistory(1);
});

document.getElementById("historyJumpForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const requested = Number(document.getElementById("historyPageInput").value);
  if (!Number.isInteger(requested)) return;
  loadHistory(Math.max(1, Math.min(historyState.totalPages, requested)));
});

fetch("/api/prediction", { headers: { Accept: "application/json" } })
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then(renderPrediction)
  .catch(showError);

loadHistory();
