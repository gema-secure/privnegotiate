export type NegotiationStatus =
  | "CREATED"
  | "COLLECTING_BOTTOMLINE"
  | "CHECKING_FEASIBILITY"
  | "INFEASIBLE"
  | "ADJUSTING"
  | "RETRY_OR_REVIEW"
  | "ASKING"
  | "ANSWERING"
  | "CLOSING"
  | "COMPUTING"
  | "UPDATING"
  | "PRIVATE_CONFIRM"
  | "BEST_EFFORT"
  | "CONCERN_COLLECTING"
  | "FINALIZED"
  | "CLOSED_WITH_CONCERN"
  | "CLOSED_UNCONFIRMED";

export type AnswerMode = "explicit" | "skip";
export type MemberRole = "initiator" | "member";
export type ConstraintKind = "exclude_item" | "opt_out" | "require_count";

/** 菜品属性的三态事实：已知含 / 已知不含 / 未知。未标注一律按 unknown 处理。 */
export type AttributeState = "present" | "absent" | "unknown";
export type AttributeSource = "import" | "manual" | "correction";

export interface CandidateAttribute {
  key: string;
  state: AttributeState;
  source: AttributeSource;
}

export interface CandidateAttributeInput {
  key: string;
  state: AttributeState;
  source?: AttributeSource;
  source_detail?: string | null;
}

export interface Candidate {
  candidate_id: string;
  name: string;
  cost_cents: number;
  category: string;
  confirmed_attributes: Record<string, string | number | boolean>;
  attributes: CandidateAttribute[];
}

export interface PublicCondition {
  budget_cents: number;
  exact_count: number;
  round_limit: number;
  member_count: number;
  category_limits: Record<string, number>;
  condition_version: number;
}

export interface PublicResult {
  result_version: number;
  candidate_ids: string[];
  total_cost_cents: number;
  source: "optimal" | "feasible" | "carried_over";
  is_baseline: boolean;
}

export interface PublicNegotiation {
  negotiation_id: number;
  title: string;
  status: NegotiationStatus;
  round_no: number;
  conditions: PublicCondition;
  candidates: Candidate[];
  recommendation: PublicResult | null;
  recommendation_pending_reason: "no_preference_evidence" | "collecting" | "computing" | null;
  pending_action: "waiting_members" | "waiting_confirmation" | "waiting_initiator" | "none";
}

export interface CreatedNegotiation {
  negotiation_id: number;
  member_id: number;
  member_token: string;
  invite_token: string;
  invite_path: string;
}

export interface JoinedMember {
  negotiation_id: number;
  member_id: number;
  display_name: string;
  role: MemberRole;
  member_token: string;
}

export interface MyQuestionBatch {
  question_batch_id: string;
  round_no: number;
  items: Candidate[];
  deadline: string;
  allow_skip: boolean;
  /** 服务端可能下发 AI 生成的措辞；字段缺席时按模板措辞理解。 */
  prompt_text?: string;
  wording_source?: "llm" | "template";
  wording_degraded?: boolean;
  wording_degraded_reason?: string | null;
}

export interface MyConstraint {
  constraint_id: string;
  kind: ConstraintKind;
  candidate_ids: string[];
  category: string | null;
  attributes: Record<string, string | number | boolean>;
  min_count: number | null;
  confirmed_version: number;
}

export interface MyAnswer {
  batch_id: string;
  round_no: number;
  mode: AnswerMode;
  evaluated_ids: string[];
  acceptable_ids: string[];
  top_id: string | null;
  accepted_at: string;
}

export interface MyConfirmation {
  result_version: number;
  decision_config_version: number;
  decision: "accept" | "reject" | null;
  decided_at: string | null;
}

export interface MyConcern {
  concern_id: number;
  result_version: number;
  text: string;
  summary: string | null;
  summary_source: "llm" | "manual" | "none";
  summary_degraded: boolean;
  summary_degraded_reason: string | null;
  status: string;
  created_at: string;
}

/** 自然语言底线解析结果：只出提议，必须成员逐条确认后才写入约束。 */
export interface BottomlineProposal {
  kind: ConstraintKind;
  candidate_ids: string[];
  category: string | null;
  min_count: number | null;
  display_text: string;
}

export interface BottomlineParse {
  needs_form: boolean;
  reason: string | null;
  degraded: boolean;
  requires_confirmation: boolean;
  model: string | null;
  proposals: BottomlineProposal[];
}

/** 本人替代要求中尚未能确认的候选属性队列；只含本人数据。 */
export interface MyPendingAttributeCheck {
  constraint_id: string;
  category: string | null;
  attribute_keys: string[];
  required_count: number;
  confirmed_count: number;
  unconfirmed_candidate_ids: string[];
}

export interface MyState {
  negotiation_id: number;
  member: { member_id: number; display_name: string; role: MemberRole };
  status: NegotiationStatus;
  round_no: number;
  conditions: PublicCondition;
  decision_config_version: number;
  question_batch: MyQuestionBatch | null;
  constraints: MyConstraint[];
  bottomline_status: "pending" | "declared" | "no_extra";
  preference_version: number;
  acceptance_summary: {
    explicit_acceptable_count: number;
    explicit_unacceptable_count: number;
    unknown_count: number;
    exact_count: number;
    acceptable_lt_k: boolean;
  };
  confirmation: MyConfirmation | null;
  concerns: MyConcern[];
  pending_attribute_checks: MyPendingAttributeCheck[];
}

export interface MyHistoryEntry {
  round_no: number;
  question_batch_id: string;
  displayed_ids: string[];
  answer: MyAnswer | null;
  result_version: number | null;
  result_candidate_ids: string[];
  result_total_cost_cents: number | null;
  explanation_code: string | null;
}

export interface SubmitAnswerResult {
  batch_id: string;
  round_no: number;
  mode: AnswerMode;
  accepted_at: string;
  duplicate: boolean;
  negotiation_status: NegotiationStatus;
}

export interface CloseRoundResult {
  round_no: number;
  negotiation_status: NegotiationStatus;
  result_version: number | null;
  stable_count: number | null;
  solver_status: string | null;
  outcome: "changed" | "infeasible" | "retry";
  message: string;
}

export interface OrganizerConcern {
  concern_id: number;
  result_version: number;
  text: string;
  summary: string | null;
  summary_source: "llm" | "manual" | "none";
  summary_degraded: boolean;
  summary_degraded_reason: string | null;
  status: string;
  created_at: string;
}

export interface OrganizerDecisionResult {
  negotiation_status: NegotiationStatus;
  action: string;
  concern_id: number | null;
}

export interface CandidateDraft {
  candidate_id: string;
  name: string;
  cost_cents: number;
  category: string;
  confirmed_attributes: Record<string, string | number | boolean>;
  attributes?: CandidateAttributeInput[];
}

export interface ConstraintsPayload {
  constraints: Array<{
    constraint_id?: string;
    kind: ConstraintKind;
    candidate_ids: string[];
    category?: string | null;
    attributes?: Record<string, string | number | boolean>;
    min_count?: number | null;
  }>;
  no_extra_bottomline: boolean;
  expected_preference_version: number;
}

export interface AnswerPayload {
  question_batch_id: string;
  request_id: string;
  mode: AnswerMode;
  evaluated_ids: string[];
  acceptable_ids: string[];
  top_id: string | null;
  reason?: string | null;
}
