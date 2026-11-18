/**
 * 轮询：首版用轮询满足"封轮计算完成后页面自动更新"（技术设计 §11.1）。
 *
 * 约定：active() 为真才继续轮询（终态自动停）；页面不可见时跳过本次但保留
 * 定时器，回到前台即恢复；组件卸载自动清理。tick 内部自行容错，抛错只
 * 静默等待下一轮，不打断已渲染的数据。
 */
import { onUnmounted } from "vue";
import { requestFailureCount } from "./api";

export function usePolling(
  tick: () => Promise<void> | void,
  active: () => boolean,
  intervalMs = 4000,
) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running = false;
  let enabled = false;
  let failures = 0;

  const schedule = () => {
    if (enabled) timer = setTimeout(() => { timer = null; void loop(); },
      Math.min(60000, intervalMs * 2 ** Math.min(failures, 6)));
  };

  const loop = async () => {
    if (!enabled || running) return;
    if (document.visibilityState === "hidden") { schedule(); return; }
    if (!active()) {
      stop();
      return;
    }
    running = true;
    const failuresBefore = requestFailureCount;
    let failed = false;
    try {
      await tick();
    } catch {
      failed = true;
    } finally {
      running = false;
    }
    failures = failed || requestFailureCount > failuresBefore ? failures + 1 : 0;
    if (!active()) stop();
    else schedule();
  };

  function start() {
    if (enabled) return;
    enabled = true;
    schedule();
  }

  function stop() {
    enabled = false;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  onUnmounted(stop);
  return { start, stop };
}

/** 协商进入这些状态后不再轮询 */
export const TERMINAL_STATUSES = new Set([
  "FINALIZED",
  "CLOSED_WITH_CONCERN",
  "CLOSED_UNCONFIRMED",
]);
