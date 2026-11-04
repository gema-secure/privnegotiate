import type {
  AnswerPayload,
  MembershipState,
  RetainedConstraints,
  RemovalRequest,
  CloseRoundResult,
  CandidateDraft,
  ConstraintsPayload,
  CreatedNegotiation,
  JoinedMember,
  MyConfirmation,
  MyHistoryEntry,
  MyState,
  NegotiationStatus,
  OrganizerConcern,
  OrganizerDecisionResult,
  PublicNegotiation,
  SubmitAnswerResult,
} from "./types";

import { clearInvalidToken } from "./session";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";
export let requestFailureCount = 0;

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  token?: string | null;
}

function describeDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return String(item);
      })
      .join("；");
  }
  return "";
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.token) headers.Authorization = `Bearer ${options.token}`;

  let response: Response;
  let text: string;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    response = await fetch(`${BASE}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
    text = await response.text();
  } catch {
    requestFailureCount++;
    throw new ApiError(controller.signal.aborted ? "REQUEST_TIMEOUT" : "NETWORK_ERROR",
      controller.signal.aborted ? "请求超时，请刷新状态后重试" : "网络不可用，请检查连接后重试", 0);
  } finally {
    clearTimeout(timeout);
  }

  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    requestFailureCount++;
    let code = `HTTP_${response.status}`;
    let message = `请求失败（${response.status}）`;
    if (data && typeof data === "object") {
      const record = data as Record<string, unknown>;
      if (typeof record.code === "string") code = record.code;
      if (typeof record.message === "string") message = record.message;
      else if (record.detail !== undefined) message = describeDetail(record.detail) || message;
    }
    if (response.status === 401 && code !== "PARTICIPATION_ENDED" && options.token) clearInvalidToken(options.token);
    throw new ApiError(code, message, response.status);
  }

  return data as T;
}

export interface CreateNegotiationBody {
  title: string;
  initiator_name: string;
  candidates: CandidateDraft[];
  budget_cents: number;
  exact_count: number;
  round_limit: number;
  category_limits?: Record<string, number>;
}

export interface MenuImageItem {
  name: string;
  price: string;
  category: string;
}

export interface MenuImageResult {
  items: MenuImageItem[];
  model: string;
  warnings: string[];
}

export const api = {
  membership(id: number, token: string) {
    return request<MembershipState>(`/negotiations/${id}/me/membership`, { token });
  },
  leave(id: number, token: string, version: number) {
    return request<void>(`/negotiations/${id}/me/leave`, { method: "POST", token, body: { expected_membership_version: version, acknowledged: true } });
  },
  requestRemoval(id: number, target: number, token: string, version: number, reason: string) {
    return request<RemovalRequest>(`/negotiations/${id}/members/${target}/removal-requests`, { method: "POST", token, body: { expected_membership_version: version, reason } });
  },
  authorizeRemoval(id: number, rid: number, token: string, version: number, approve: boolean) {
    return request<RemovalRequest>(`/negotiations/${id}/removal-requests/${rid}/me/authorization`, { method: "POST", token, body: { expected_membership_version: version, approve, consequences_acknowledged: true } });
  },
  cancelRemoval(id: number, rid: number, token: string, version: number) {
    return request<void>(`/negotiations/${id}/removal-requests/${rid}/cancel`, { method: "POST", token, body: { expected_membership_version: version } });
  },
  markUnresponsive(id: number, target: number, token: string, version: number) {
    return request<void>(`/negotiations/${id}/members/${target}/unresponsive`, { method: "POST", token, body: { expected_membership_version: version } });
  },
  returnToActive(id: number, token: string) {
    return request<void>(`/negotiations/${id}/me/return`, { method: "POST", token });
  },
  retainedConstraints(id: number, token: string) {
    return request<RetainedConstraints>(`/negotiations/${id}/me/retained-constraints`, { token });
  },
  withdrawRetained(id: number, cid: string, token: string, version: number) {
    return request<RetainedConstraints>(`/negotiations/${id}/me/retained-constraints/${encodeURIComponent(cid)}/withdraw`, { method: "POST", token, body: { expected_preference_version: version, acknowledged: true } });
  },
  parseMenuImage(imageBase64: string, mimeType: "image/jpeg" | "image/png" | "image/webp", token: string) {
    return request<MenuImageResult>("/menu/parse-image", {
      method: "POST",
      body: { image_base64: imageBase64, mime_type: mimeType },
      token,
    });
  },
  createNegotiation(body: CreateNegotiationBody) {
    return request<CreatedNegotiation>("/negotiations", { method: "POST", body });
  },
  join(negotiationId: number, body: { invite_token: string; display_name: string }) {
    return request<JoinedMember>(`/negotiations/${negotiationId}/members`, {
      method: "POST",
      body,
    });
  },
  publicView(negotiationId: number, token: string) {
    return request<PublicNegotiation>(`/negotiations/${negotiationId}/public`, { token });
  },
  myState(negotiationId: number, token: string) {
    return request<MyState>(`/negotiations/${negotiationId}/me`, { token });
  },
  confirmConstraints(negotiationId: number, token: string, body: ConstraintsPayload) {
    return request<MyState>(`/negotiations/${negotiationId}/me/constraints`, {
      method: "POST",
      body,
      token,
    });
  },
  submitAnswer(negotiationId: number, token: string, body: AnswerPayload) {
    return request<SubmitAnswerResult>(`/negotiations/${negotiationId}/me/answers`, {
      method: "POST",
      body,
      token,
    });
  },
  revise(negotiationId: number, token: string, body: { request_id: string; candidate_id: string; acceptable: boolean | null }) {
    return request<MyState>(`/negotiations/${negotiationId}/me/revisions`, {
      method: "POST",
      body,
      token,
    });
  },
  history(negotiationId: number, token: string) {
    return request<MyHistoryEntry[]>(`/negotiations/${negotiationId}/me/history`, { token });
  },
  closeRound(negotiationId: number, roundNo: number, token: string) {
    return request<CloseRoundResult>(`/negotiations/${negotiationId}/rounds/${roundNo}/close`, {
      method: "POST",
      token,
    });
  },
  confirmResult(
    negotiationId: number,
    resultVersion: number,
    token: string,
    body: { decision_config_version: number; accept: boolean; reason?: string | null },
  ) {
    return request<MyConfirmation>(
      `/negotiations/${negotiationId}/results/${resultVersion}/me/confirmation`,
      { method: "POST", body, token },
    );
  },
  submitConcern(
    negotiationId: number,
    token: string,
    body: { result_version: number; text: string; share_acknowledged: true },
  ) {
    return request<MyState["concerns"][number]>(`/negotiations/${negotiationId}/me/concerns`, {
      method: "POST",
      body,
      token,
    });
  },
  organizerConcerns(negotiationId: number, token: string) {
    return request<OrganizerConcern[]>(`/negotiations/${negotiationId}/organizer/concerns`, {
      token,
    });
  },
  organizerDecision(
    negotiationId: number,
    token: string,
    body: {
      action: "continue" | "stop";
      concern_id?: number | null;
      extra_rounds?: number | null;
      public_adjustment?: {
        budget_cents?: number;
        exact_count?: number;
        category_limits?: Record<string, number>;
      } | null;
    },
  ) {
    return request<OrganizerDecisionResult>(`/negotiations/${negotiationId}/organizer/decision`, {
      method: "POST",
      body,
      token,
    });
  },
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "操作失败，请稍后重试";
}

export type { NegotiationStatus };
