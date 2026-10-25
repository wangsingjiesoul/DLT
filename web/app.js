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
  if (item.selection_reason) return `AI：${item.selection_reason}`;
  if (index === 0) return "冷热混合 · 三区覆盖";
  if (index === 1) return "近期频率 · 重复惩罚";
  return "遗漏因子 · 多样化";
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

fetch("/api/prediction", { headers: { Accept: "application/json" } })
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then(renderPrediction)
  .catch(showError);
