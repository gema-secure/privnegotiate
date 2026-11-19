/**
 * 对话投影：把协商的公开状态 + 本人状态推导成「小议」仲裁者的对话流。
 *
 * 纯函数、无持久化——消息永远与服务器状态一致；轮询刷新时
 * 新状态自然成为新消息。不发明服务器上不存在的事件。
 */
import type { MyHistoryEntry, MyState, PublicNegotiation } from "../types";
import { yuan } from "../format";

export type ChatSide = "yi" | "me";

export interface ProposalCardData {
  version: number;
  names: string[];
  totalCents: number;
  budgetCents: number;
  count: number;
  exactCount: number;
}

export interface ChatMessage {
  id: string;
  side: ChatSide;
  kind: "text" | "proposal" | "ask" | "notice" | "reply-summary";
  text?: string;
  card?: ProposalCardData;
  round?: number;
  pending?: boolean;
}

export function proposalCardFrom(
  version: number,
  names: string[],
  totalCents: number,
  budgetCents: number,
  exactCount: number,
): ProposalCardData {
  return { version, names, totalCents, budgetCents, count: names.length, exactCount };
}

function conditionsText(pub: PublicNegotiation): string {
  const lines = [
    `预算不超过 ¥${yuan(pub.conditions.budget_cents)}`,
    `恰选 ${pub.conditions.exact_count} 项`,
    `轮数上限 ${pub.conditions.round_limit} 轮`,
  ];
  const limits = Object.entries(pub.conditions.category_limits ?? {});
  for (const [category, limit] of limits.slice(0, 3)) {
    lines.push(`${category}不超过 ${limit} 项`);
  }
  return lines.join("；") + "。";
}

function namesByIds(pub: PublicNegotiation, ids: string[]): string[] {
  const map = new Map(pub.candidates.map((item) => [item.candidate_id, item.name]));
  return ids.map((id) => map.get(id) ?? id);
}

/** 全量对话流：公告（公共）与问答（本人）按轮次交错。 */
export function deriveMessages(
  pub: PublicNegotiation,
  me: MyState | null,
  history: MyHistoryEntry[],
): ChatMessage[] {
  const messages: ChatMessage[] = [];

  messages.push({
    id: "intro",
    side: "yi",
    kind: "text",
    text: `大家好，我是小议。这次一起决定「${pub.title}」。先说好规则：${conditionsText(pub)}`,
  });
  messages.push({
    id: "intro-privacy",
    side: "yi",
    kind: "text",
    text: "每个人的回答只对你和我可见，成员之间互相看不到。有底线也只告诉我。",
  });

  // 底线阶段
  if (pub.status === "COLLECTING_BOTTOMLINE" || history.length === 0) {
    if (me && me.bottomline_status === "pending") {
      messages.push({
        id: "ask-bottomline",
        side: "yi",
        kind: "ask",
        text: "先私下确认一下：有没有整组必须避开、或你本人不参与的候选？",
      });
    } else if (pub.status === "COLLECTING_BOTTOMLINE") {
      messages.push({
        id: "bottomline-wait",
        side: "yi",
        kind: "text",
        text: "我在等其他成员确认底线，人齐了就开始第一轮。",
      });
    }
  }

  // 历史轮次：我的作答 → 轮次结果公告
  const byRound = new Map<number, MyHistoryEntry>();
  for (const entry of history) byRound.set(entry.round_no, entry);
  const maxHistoryRound = history.reduce((acc, item) => Math.max(acc, item.round_no), 0);
  const maxHistoryVersion = history.reduce(
    (acc, item) => Math.max(acc, item.result_version ?? 0),
    0,
  );
  const inConfirm = ["PRIVATE_CONFIRM", "BEST_EFFORT"].includes(pub.status);

  for (let round = 1; round <= maxHistoryRound; round += 1) {
    const entry = byRound.get(round);
    if (!entry) continue;

    if (entry.answer) {
      if (entry.answer.mode === "skip") {
        messages.push({
          id: `me-${round}`,
          side: "me",
          kind: "reply-summary",
          round,
          text: "（本轮先跳过）",
        });
      } else if (entry.answer.acceptable_ids.length === 0) {
        messages.push({
          id: `me-${round}`,
          side: "me",
          kind: "reply-summary",
          round,
          text: "这些我都不能接受。",
        });
      } else {
        const names = namesByIds(pub, entry.answer.acceptable_ids);
        const topName = entry.answer.top_id
          ? namesByIds(pub, [entry.answer.top_id])[0]
          : null;
        messages.push({
          id: `me-${round}`,
          side: "me",
          kind: "reply-summary",
          round,
          text: `能接受：${names.join("、")}。最想要${topName ? `「${topName}」` : "暂不指定"}。`,
        });
      }
    }

    if (entry.result_version && entry.result_candidate_ids.length) {
      messages.push({
        id: `round-${round}-result`,
        side: "yi",
        kind: "proposal",
        round,
        text: `第 ${round} 轮的提案出来了，大家看一眼：`,
        card: proposalCardFrom(
          entry.result_version,
          namesByIds(pub, entry.result_candidate_ids),
          entry.result_total_cost_cents ?? 0,
          pub.conditions.budget_cents,
          pub.conditions.exact_count,
        ),
      });
    }
  }

  // 最新公共提案：未出现在本人历史里时补一条（匿名访客 / 结束状态 / 公共与历史的时间差）
  const rec = pub.recommendation;
  const recIsNew = !!rec && rec.result_version > maxHistoryVersion;
  if (rec && recIsNew && !inConfirm) {
    messages.push({
      id: `rec-${rec.result_version}`,
      side: "yi",
      kind: "proposal",
      text: "当前的提案出来了，大家看一眼：",
      card: proposalCardFrom(
        rec.result_version,
        namesByIds(pub, rec.candidate_ids),
        rec.total_cost_cents,
        pub.conditions.budget_cents,
        pub.conditions.exact_count,
      ),
    });
  }

  // 当前轮：未答则小议发问
  const currentEntry = byRound.get(pub.round_no);
  const answeredCurrent = !!(currentEntry && currentEntry.answer);
  if (
    me &&
    me.question_batch &&
    !answeredCurrent &&
    ["ASKING", "ANSWERING"].includes(pub.status)
  ) {
    messages.push({
      id: `ask-${pub.round_no}`,
      side: "yi",
      kind: "ask",
      round: pub.round_no,
      text: `第 ${pub.round_no} 轮，只问你 ${me.question_batch.items.length} 个：这些里哪些你能接受？点下面气泡告诉我，选完发送。`,
    });
  } else if (["ASKING", "ANSWERING"].includes(pub.status) && me && answeredCurrent) {
    messages.push({
      id: `wait-${pub.round_no}`,
      side: "yi",
      kind: "text",
      round: pub.round_no,
      text: "收到你的回答。等本轮结束后我会给出新的提案，到时这里会自动更新。",
    });
  }

  // 计算中
  if (["CLOSING", "COMPUTING", "UPDATING", "CHECKING_FEASIBILITY"].includes(pub.status)) {
    messages.push({
      id: "computing",
      side: "yi",
      kind: "text",
      pending: true,
      text: "正在按大家已确认的条件计算新组合……",
    });
  }

  // 无解 / 重试
  if (pub.status === "INFEASIBLE") {
    messages.push({
      id: "infeasible",
      side: "yi",
      kind: "notice",
      text: "当前的公共条件凑不出可行组合。需要发起者调整预算或数量；任何人的底线我都不会自动放宽。",
    });
  }
  if (pub.status === "RETRY_OR_REVIEW") {
    messages.push({
      id: "retry",
      side: "yi",
      kind: "notice",
      text: "这次计算没有完成，我不能说“无解”。已确认的条件都在，稍后重试。",
    });
  }

  // 确认阶段
  if (["PRIVATE_CONFIRM", "BEST_EFFORT"].includes(pub.status)) {
    if (pub.recommendation) {
      messages.push({
        id: "confirm-ask",
        side: "yi",
        kind: "ask",
        text:
          pub.status === "BEST_EFFORT"
            ? "已到轮数上限，当前方案需要每个人私下确认——稳定不等于同意。"
            : "方案连续两轮没变，需要每个人私下确认——稳定不等于同意。",
        // 本人历史里已展示过该版本时不重复发卡（匿名访客才需要卡片）
        card:
          pub.recommendation.result_version > maxHistoryVersion
            ? proposalCardFrom(
                pub.recommendation.result_version,
                namesByIds(pub, pub.recommendation.candidate_ids),
                pub.recommendation.total_cost_cents,
                pub.conditions.budget_cents,
                pub.conditions.exact_count,
              )
            : undefined,
      });
    }
  }

  if (pub.status === "CONCERN_COLLECTING") {
    messages.push({
      id: "concern",
      side: "yi",
      kind: "notice",
      text: "有成员提出了顾虑（内容只交给发起者，默认不附身份）。等发起者决定继续还是结束。",
    });
  }

  if (pub.status === "FINALIZED") {
    messages.push({
      id: "final",
      side: "yi",
      kind: "text",
      text: "当前参与成员都明确接受了这一版。确认范围不含已离开或被移除成员，历史异议继续保留。",
    });
  }
  if (pub.status === "CLOSED_WITH_CONCERN") {
    messages.push({
      id: "closed-concern",
      side: "yi",
      kind: "notice",
      text: "协商结束，但有顾虑被保留。这一版不代表全员同意。",
    });
  }
  if (pub.status === "CLOSED_UNCONFIRMED") {
    messages.push({
      id: "closed-unconfirmed",
      side: "yi",
      kind: "notice",
      text: "协商结束，未获得全员确认。结果按结束状态记录。",
    });
  }

  return messages;
}
