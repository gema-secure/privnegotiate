import { computed, onUnmounted, ref } from "vue";
import { api, ApiError, errorMessage } from "./api";
import { loadSession, session } from "./session";
import { TERMINAL_STATUSES, usePolling } from "./polling";
import type { MyHistoryEntry, MyState, PublicNegotiation } from "./types";

export function useNegotiationState(id: number, options: { personal?: boolean; history?: boolean } = {}) {
  loadSession(id);
  const pub = ref<PublicNegotiation | null>(null);
  const me = ref<MyState | null>(null);
  const history = ref<MyHistoryEntry[]>([]);
  const loading = ref(true);
  const error = ref("");
  const token = computed(() => session.negotiationId === id ? session.current?.token ?? null : null);
  const invited = computed(() => !!token.value);
  const isInitiator = computed(() => invited.value && session.current?.role === "initiator");
  const canCloseRound = computed(() => isInitiator.value && !!pub.value &&
    ["ASKING", "ANSWERING", "CLOSING", "CHECKING_FEASIBILITY", "RETRY_OR_REVIEW"].includes(pub.value.status));
  let generation = 0;
  let disposed = false;
  let authorizationFailed = false;
  onUnmounted(() => { disposed = true; generation++; });
  const clear = () => { pub.value = null; me.value = null; history.value = []; };

  async function refresh() {
    authorizationFailed = false;
    const current = ++generation;
    const memberToken = token.value;
    error.value = "";
    if (!memberToken) {
      clear();
      error.value = "成员身份已失效或尚未加入，请通过邀请链接重新加入。";
      loading.value = false;
      return;
    }
    try {
      const [publicState, myState, entries] = await Promise.all([
        api.publicView(id, memberToken),
        options.personal === false ? Promise.resolve(null) : api.myState(id, memberToken),
        options.history ? api.history(id, memberToken) : Promise.resolve([]),
      ]);
      if (disposed || current !== generation || memberToken !== token.value) return;
      pub.value = publicState;
      me.value = myState;
      history.value = entries;
    } catch (err) {
      if (disposed || current !== generation) return;
      if (err instanceof ApiError && [401, 403].includes(err.status)) {
        authorizationFailed = true;
        clear();
      }
      error.value = errorMessage(err);
    } finally {
      if (!disposed && current === generation) loading.value = false;
    }
  }
  const polling = usePolling(refresh, () => !authorizationFailed && !!token.value &&
    (!pub.value || !TERMINAL_STATUSES.has(pub.value.status)));
  return { pub, me, history, loading, error, token, invited, isInitiator, canCloseRound, refresh, polling };
}
