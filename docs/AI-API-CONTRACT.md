# AI 主线三模块 API 契约（前端接入最小说明）

> 适用范围：底线解析、追问措辞、顾虑整理。三条路径都只做辅助，不参与打分、不修改硬约束、不代替成员确认。
> 现实状态：华为云 MaaS 文本凭证尚未取得，`LLM_TEXT_ENABLED` 默认 `false`。后端已按下方契约实现，但真实 MaaS 端点尚未验证；凡返回 `degraded=true` 均表示走了确定性降级路径。

## 通用约定

- 鉴权：`Authorization: Bearer <member_token>`。
- 时间字段为 ISO 8601；金额为整数分。
- 降级原因枚举：`llm_disabled`（未启用/无凭证）、`llm_unavailable`（超时、不可达、熔断）、`llm_bad_output`（输出不合规）、`empty_input`、`no_valid_proposals`。
- 前端不得因为 `degraded=true` 阻断流程，应按字段提示「AI 辅助不可用，已使用固定模板/结构化表单」。

## 1. 底线自然语言解析（只出提议，不落库）

`POST /negotiations/{negotiation_id}/me/bottomline/parse`

请求：

```json
{ "text": "我不想吃 c01，另外至少来一道 main" }
```

响应：

```json
{
  "needs_form": false,
  "reason": null,
  "degraded": false,
  "requires_confirmation": true,
  "model": "qwen2.5-72b-instruct",
  "proposals": [
    { "kind": "opt_out", "candidate_ids": ["c01"], "category": null, "min_count": null, "display_text": "本人不参与：c01" },
    { "kind": "require_count", "candidate_ids": [], "category": "main", "min_count": 1, "display_text": "至少 1 项本人可参与的「main」" }
  ]
}
```

降级响应（未配置凭证时）：

```json
{
  "needs_form": true,
  "reason": "llm_disabled",
  "degraded": true,
  "requires_confirmation": true,
  "model": null,
  "proposals": []
}
```

- `kind` 仅允许 `exclude_item` / `opt_out` / `require_count`；候选只能来自当前协商的 active 候选。
- 本接口不写任何约束。成员修改/勾选提议后，仍通过 `POST /negotiations/{id}/me/constraints` 确认落库。
- 降级时前端展示现有结构化底线表单。

## 2. 追问措辞（只表达，不决定问什么）

`GET /negotiations/{negotiation_id}/me?wording=auto|template`

`wording` 默认 `auto`；`template` 强制使用确定性模板，供前端人工覆写。

当有进行中的提问批次时，`question_batch` 新增字段：

```json
{
  "prompt_text": "请针对以下候选作答：菜品1、菜品2。……",
  "wording_source": "template",
  "wording_degraded": true,
  "wording_degraded_reason": "llm_disabled"
}
```

- `items`、数量与顺序完全由服务端冻结，`prompt_text` 只做语言表达，可能不包含逐项明细，前端仍应以 `items` 渲染候选项。
- `wording_source=llm` 时 `prompt_text` 已校验包含全部候选名称；否则自动回退模板。
- `wording=template` 时 `wording_degraded=false`（这是人工选择，不是故障）。
- 问题预算、每轮题量、轮次上限不受本模块影响。

## 3. 顾虑整理

提交顾虑：`POST /negotiations/{negotiation_id}/me/concerns`

请求不变：

```json
{ "result_version": 1, "text": "有一道菜我不能吃，可能需要换。", "share_acknowledged": true }
```

响应新增字段：

```json
{
  "summary": "成员担心其中一道菜不合口味，建议替换后再确认。",
  "summary_source": "llm",
  "summary_degraded": false,
  "summary_degraded_reason": null
}
```

降级响应：

```json
{
  "summary": null,
  "summary_source": "none",
  "summary_degraded": true,
  "summary_degraded_reason": "llm_unavailable"
}
```

发起者列表：`GET /negotiations/{negotiation_id}/organizer/concerns` 同样返回
`summary_source`（`llm` / `manual` / `none`）、`summary_degraded`、`summary_degraded_reason`。

人工修正摘要（仅发起者）：`PATCH /negotiations/{negotiation_id}/organizer/concerns/{concern_id}/summary`

```json
{ "summary": "成员希望更换其中一道菜，未说明具体菜名。" }
```

响应为修正后的顾虑对象，`summary_source` 变为 `manual`，`text`（原文）不变。修正记录写入事件流 `CONCERN_SUMMARY_CORRECTED`。

## 边界与验证说明

- 三模块均不写硬约束、不改问题预算、不参与打分；底线解析结果必须由成员确认。
- 已用假客户端完成业务路径测试（正常/未启用/调用失败/坏输出）；未对真实 MaaS 端点做连通验证，材料中不得表述为「已接入成功」。
- 配置项：`LLM_TEXT_ENABLED=false`、`MAAS_API_KEY`、`MAAS_BASE_URL`、`MAAS_TEXT_MODEL`。缺 `MAAS_API_KEY` 时即使开启也会按未启用降级。
