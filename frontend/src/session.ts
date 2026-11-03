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

function storageKey(negotiationId: number): string {
  return `hey-i.member.${negotiationId}`;
}

export function getSession(negotiationId: number): MemberSession | null {
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
  localStorage.setItem(storageKey(negotiationId), JSON.stringify(value));
  session.negotiationId = negotiationId;
  session.current = value;
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
