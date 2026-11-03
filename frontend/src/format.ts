import type { NegotiationStatus } from "./types";

const STATUS_LABELS: Record<NegotiationStatus, string> = {
  CREATED: "已创建",
  COLLECTING_BOTTOMLINE: "收集底线中",
  CHECKING_FEASIBILITY: "检查可行性",
  INFEASIBLE: "暂无可行组合",
  ADJUSTING: "调整条件中",
  RETRY_OR_REVIEW: "等待重试",
  ASKING: "等待作答",
  ANSWERING: "作答进行中",
  CLOSING: "封轮中",
  COMPUTING: "计算中",
  UPDATING: "更新中",
  PRIVATE_CONFIRM: "待私下确认",
  BEST_EFFORT: "尚未收敛，待确认",
  CONCERN_COLLECTING: "顾虑待处理",
  FINALIZED: "当前参与成员已确认",
  CLOSED_WITH_CONCERN: "已结束，保留顾虑",
  CLOSED_UNCONFIRMED: "已结束，未获全员确认",
};

const STATUS_TONES: Record<NegotiationStatus, "green" | "amber" | "red" | "muted"> = {
  CREATED: "muted",
  COLLECTING_BOTTOMLINE: "amber",
  CHECKING_FEASIBILITY: "muted",
  INFEASIBLE: "red",
  ADJUSTING: "amber",
  RETRY_OR_REVIEW: "amber",
  ASKING: "green",
  ANSWERING: "green",
  CLOSING: "muted",
  COMPUTING: "muted",
  UPDATING: "muted",
  PRIVATE_CONFIRM: "amber",
  BEST_EFFORT: "amber",
  CONCERN_COLLECTING: "amber",
  FINALIZED: "green",
  CLOSED_WITH_CONCERN: "amber",
  CLOSED_UNCONFIRMED: "red",
};

export function statusLabel(status: NegotiationStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function statusTone(status: NegotiationStatus): "green" | "amber" | "red" | "muted" {
  return STATUS_TONES[status] ?? "muted";
}

export function yuan(cents: number): string {
  const value = cents / 100;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function parseYuan(input: string): number {
  const value = Number.parseFloat(input);
  if (!Number.isFinite(value) || value < 0) return Number.NaN;
  return Math.round(value * 100);
}

export function shortDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function requestId(): string {
  const raw =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random()}`;
  return raw.replace(/[^A-Za-z0-9]/g, "").slice(0, 40);
}
