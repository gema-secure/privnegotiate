import { yuan } from "./format";

export interface ShareCardItem {
  name: string;
  costCents: number;
}

export interface ShareCardData {
  title: string;
  statusLabel: string;
  items: ShareCardItem[];
  totalCents: number;
  budgetCents: number;
  /** 顶部横幅语气，按协商场景自适应；缺省用通用语气 */
  banner?: string;
}

const FONT = '"PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif';
const WIDTH = 720;
const PADDING = 44;

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + width, y, x + width, y + height, radius);
  ctx.arcTo(x + width, y + height, x, y + height, radius);
  ctx.arcTo(x, y + height, x, y, radius);
  ctx.arcTo(x, y, x + width, y, radius);
  ctx.closePath();
}

function wrapTitle(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const char of text) {
    const next = line + char;
    if (ctx.measureText(next).width > maxWidth && line) {
      lines.push(line);
      line = char;
    } else {
      line = next;
    }
  }
  if (line) lines.push(line);
  return lines.slice(0, 2);
}

export async function renderShareCard(data: ShareCardData): Promise<Blob> {
  const itemHeight = 60;
  const titleLines = 2;
  const height =
    300 + titleLines * 46 + 70 + data.items.length * itemHeight + 190 + 90;

  const canvas = document.createElement("canvas");
  canvas.width = WIDTH;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("当前浏览器不支持生成图片");

  ctx.fillStyle = "#F5F6F2";
  ctx.fillRect(0, 0, WIDTH, height);

  ctx.fillStyle = "#245E50";
  ctx.fillRect(0, 0, WIDTH, 168);
  ctx.fillStyle = "rgba(255,255,255,0.92)";
  ctx.font = `600 20px ${FONT}`;
  ctx.fillText("和议 HEYI", PADDING, 62);
  ctx.fillStyle = "rgba(255,255,255,0.62)";
  ctx.font = `14px ${FONT}`;
  ctx.fillText("听见每一种偏好，让相聚刚刚好", PADDING, 92);
  ctx.fillStyle = "rgba(255,255,255,0.16)";
  ctx.fillRect(PADDING, 116, WIDTH - PADDING * 2, 1);
  ctx.fillStyle = "#FFFFFF";
  ctx.font = `700 30px ${FONT}`;
  ctx.fillText(data.banner ?? "这次，一起定", PADDING, 152);

  const cardX = PADDING - 6;
  const cardY = 214;
  const cardWidth = WIDTH - (PADDING - 6) * 2;
  const cardHeight = height - cardY - 56;

  ctx.save();
  ctx.shadowColor = "rgba(36,94,80,0.12)";
  ctx.shadowBlur = 24;
  ctx.shadowOffsetY = 8;
  ctx.fillStyle = "#FFFFFF";
  roundRect(ctx, cardX, cardY, cardWidth, cardHeight, 22);
  ctx.fill();
  ctx.restore();

  const contentX = cardX + 30;
  const contentWidth = cardWidth - 60;

  ctx.fillStyle = "#1E2A26";
  ctx.font = `700 30px ${FONT}`;
  const lines = wrapTitle(ctx, data.title, contentWidth);
  lines.forEach((line, index) => {
    ctx.fillText(line, contentX, cardY + 56 + index * 42);
  });

  let cursorY = cardY + 56 + lines.length * 42 + 22;
  ctx.fillStyle = "#7C8A84";
  ctx.font = `14px ${FONT}`;
  ctx.fillText(data.statusLabel, contentX, cursorY);
  cursorY += 26;
  ctx.strokeStyle = "#E4E8E0";
  ctx.beginPath();
  ctx.moveTo(contentX, cursorY);
  ctx.lineTo(contentX + contentWidth, cursorY);
  ctx.stroke();
  cursorY += 34;

  data.items.forEach((item, index) => {
    ctx.fillStyle = "#8A968F";
    ctx.font = `14px ${FONT}`;
    ctx.fillText(String(index + 1).padStart(2, "0"), contentX, cursorY - 6);
    ctx.fillStyle = "#1E2A26";
    ctx.font = `500 21px ${FONT}`;
    ctx.fillText(item.name, contentX + 34, cursorY);
    ctx.textAlign = "right";
    ctx.fillStyle = "#245E50";
    ctx.font = `600 20px ${FONT}`;
    ctx.fillText(`¥ ${yuan(item.costCents)}`, contentX + contentWidth, cursorY);
    ctx.textAlign = "left";
    cursorY += itemHeight;
    if (index < data.items.length - 1) {
      ctx.strokeStyle = "#F0F2EC";
      ctx.beginPath();
      ctx.moveTo(contentX + 34, cursorY - 24);
      ctx.lineTo(contentX + contentWidth, cursorY - 24);
      ctx.stroke();
    }
  });

  cursorY += 8;
  ctx.fillStyle = "#F3F6F1";
  roundRect(ctx, contentX - 10, cursorY, contentWidth + 20, 96, 14);
  ctx.fill();
  ctx.fillStyle = "#5C6A63";
  ctx.font = `14px ${FONT}`;
  ctx.fillText("方案总费用", contentX + 6, cursorY + 34);
  ctx.fillStyle = "#1E2A26";
  ctx.font = `700 30px ${FONT}`;
  ctx.fillText(`¥ ${yuan(data.totalCents)}`, contentX + 6, cursorY + 72);
  ctx.textAlign = "right";
  ctx.fillStyle = "#8A968F";
  ctx.font = `14px ${FONT}`;
  ctx.fillText(`预算 ¥ ${yuan(data.budgetCents)}`, contentX + contentWidth, cursorY + 34);
  ctx.fillText(`${data.items.length} 项组合`, contentX + contentWidth, cursorY + 70);
  ctx.textAlign = "left";

  ctx.fillStyle = "#8A968F";
  ctx.font = `13px ${FONT}`;
  ctx.fillText("由和议协商生成 · 不含任何成员的个人回答", contentX, height - 34);

  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("图片生成失败"));
    }, "image/png");
  });
}

interface ShareNavigator {
  canShare?: (data: { files?: File[] }) => boolean;
  share?: (data: { files?: File[]; title?: string; text?: string }) => Promise<void>;
}

export async function shareOrDownloadCard(
  data: ShareCardData,
): Promise<"shared" | "downloaded"> {
  const blob = await renderShareCard(data);
  const fileName = `和议-${data.title.replace(/[\\/:*?"<>|]/g, "").slice(0, 20) || "方案"}.png`;
  const file = new File([blob], fileName, { type: "image/png" });
  const nav = navigator as Navigator & ShareNavigator;
  if (nav.canShare?.({ files: [file] }) && nav.share) {
    await nav.share({ files: [file], title: data.title, text: "和议协商结果" });
    return "shared";
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
  return "downloaded";
}
