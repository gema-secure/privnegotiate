/**
 * 轮询：首版用轮询满足"封轮计算完成后页面自动更新"（技术设计 §11.1）。
 *
 * 约定：active() 为真才继续轮询（终态自动停）；页面不可见时跳过本次但保留
 * 定时器，回到前台即恢复；组件卸载自动清理。tick 内部自行容错，抛错只
 * 静默等待下一轮，不打断已渲染的数据。
 */
import { onUnmounted } from "vue";

export function usePolling(
  tick: () => Promise<void> | void,
  active: () => boolean,
  intervalMs = 4000,
) {
  let timer: ReturnType<typeof setInterval> | null = null;
  let running = false;

  const loop = async () => {
    if (running || document.visibilityState === "hidden") return;
    if (!active()) {
      stop();
      return;
    }
    running = true;
    try {
      await tick();
    } catch {
      // 网络抖动等瞬态错误：保留页面现有数据，下一轮再试
    } finally {
      running = false;
    }
    if (!active()) stop();
  };

  function start() {
    if (timer !== null) return;
    timer = setInterval(() => {
      void loop();
    }, intervalMs);
  }

  function stop() {
    if (timer !== null) {
      clearInterval(timer);
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
