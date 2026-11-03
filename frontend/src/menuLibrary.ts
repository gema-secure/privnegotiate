import type { ParsedMenuRow } from "./menuImport";

export interface MenuTemplate {
  id: string;
  name: string;
  savedAt: string;
  rows: ParsedMenuRow[];
}

const STORAGE_KEY = "hey-i.menus";
const MAX_TEMPLATES = 20;

function read(): MenuTemplate[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as MenuTemplate[]) : [];
  } catch {
    return [];
  }
}

function write(items: MenuTemplate[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    // 本地存储不可用时静默降级，不影响导入流程
  }
}

export function listMenus(): MenuTemplate[] {
  return read().sort((left, right) => right.savedAt.localeCompare(left.savedAt));
}

export function saveMenu(name: string, rows: ParsedMenuRow[]): MenuTemplate {
  const template: MenuTemplate = {
    id: `menu-${Date.now().toString(36)}`,
    name: name.trim().slice(0, 40) || "未命名菜单",
    savedAt: new Date().toISOString(),
    rows: rows.map((row) => ({ ...row })),
  };
  write([template, ...read()].slice(0, MAX_TEMPLATES));
  return template;
}

export function removeMenu(id: string) {
  write(read().filter((item) => item.id !== id));
}
