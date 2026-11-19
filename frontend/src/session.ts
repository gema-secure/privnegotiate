import { reactive } from "vue";
import type { MemberRole } from "./types";

export interface MemberSession {
  memberId: number;
  token: string;
  displayName: string;
  role: MemberRole;
  inviteToken?: string;
}

interface SessionState {
  negotiationId: number | null;
  current: MemberSession | null;
}

export const session = reactive<SessionState>({ negotiationId: null, current: null });
export const sessionNotice = reactive({ message: "", rejoinUrl: "" });
const memorySessions = new Map<number, MemberSession>();
const invalidTokens = new Set<string>();

function storageKey(negotiationId: number): string {
  return `hey-i.member.${negotiationId}`;
}

export function getSession(negotiationId: number): MemberSession | null {
  if (memorySessions.has(negotiationId)) return memorySessions.get(negotiationId)!;
  try {
    const raw = localStorage.getItem(storageKey(negotiationId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<MemberSession>;
    if (
      typeof parsed.memberId !== "number" ||
      typeof parsed.token !== "string" ||
      !parsed.token
    ) {
      return null;
    }
    if (invalidTokens.has(parsed.token)) return null;
    return {
      memberId: parsed.memberId,
      token: parsed.token,
      displayName: typeof parsed.displayName === "string" ? parsed.displayName : "成员",
      role: parsed.role === "initiator" ? "initiator" : "member",
      inviteToken:
        typeof parsed.inviteToken === "string" && parsed.inviteToken
          ? parsed.inviteToken
          : undefined,
    };
  } catch {
    return null;
  }
}

export function saveSession(negotiationId: number, value: MemberSession): void {
  sessionNotice.message = "";
  sessionNotice.rejoinUrl = "";
  memorySessions.set(negotiationId, value);
  try {
    localStorage.setItem(storageKey(negotiationId), JSON.stringify(value));
  } catch {
    sessionNotice.message = "身份已暂存在本页。浏览器无法保存身份，请勿刷新或关闭此页；允许网站存储后可重试保存。";
  }
  session.negotiationId = negotiationId;
  session.current = value;
}

export function clearInvalidToken(token: string): void {
  const ids = new Set(memorySessions.keys());
  if (session.negotiationId !== null) ids.add(session.negotiationId);
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith("hey-i.member.")) ids.add(Number(key.slice("hey-i.member.".length)));
    }
  } catch { /* Memory sessions still work. */ }
  for (const id of ids) {
    const value = getSession(id);
    if (value?.token !== token) continue;
    memorySessions.delete(id);
    try { localStorage.removeItem(storageKey(id)); } catch { /* Storage unavailable. */ }
    if (session.negotiationId === id && session.current?.token === token) {
      session.current = null;
      sessionNotice.message = "成员身份已失效，请通过邀请链接重新加入。";
      sessionNotice.rejoinUrl = value.inviteToken ? `/join/${id}/${encodeURIComponent(value.inviteToken)}` : "";
    }
  }
  invalidTokens.add(token);
}

export function loadSession(negotiationId: number): MemberSession | null {
  const value = getSession(negotiationId);
  session.negotiationId = negotiationId;
  session.current = value;
  return value;
}

export function isInitiator(negotiationId: number): boolean {
  return getSession(negotiationId)?.role === "initiator";
}
