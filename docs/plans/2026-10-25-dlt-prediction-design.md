# 大乐透推荐界面 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 设计一个明确展示大乐透候选号码、统计依据、回测状态和风险提示的 SVG 界面原型。

**Architecture:** 前端原型只负责展示模型输出，不宣称可以确定预测随机开奖结果。未来后端按“数据校验 → 滚动特征 → 时间序列回测 → 候选评分 → 约束采样 → 解释输出”生成推荐结果，前端消费结构化 JSON。

**Tech Stack:** SVG、Python 标准库、JSON、GitHub Actions（后续自动化阶段）

---

### Task 1: 预测界面原型

**Files:**
- Create: `design/dlt-prediction-dashboard.svg`

**Step 1:** 使用当前 2,939 期大乐透数据的最新期号和滚动统计填充示例内容。

**Step 2:** 展示三组不同策略的候选号码，并区分前区与后区。

**Step 3:** 展示热度、遗漏、结构和组合差异度等解释指标。

**Step 4:** 显示随机性声明、理论组合总数和预算提醒。

**Step 5:** 渲染 SVG，检查中文、间距、对比度和号码可读性。

### Task 2: 后端设计说明

**Files:**
- No code changes in this preview phase.

**Step 1:** 定义数据校验、特征工程、走步回测和候选生成流程。

**Step 2:** 将模型表现与等权随机基线比较，避免使用数据泄漏或伪准确率。

**Step 3:** 规定后端返回每组候选号码、策略标签、因素得分和风险说明。

