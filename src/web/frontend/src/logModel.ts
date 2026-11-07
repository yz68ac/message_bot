export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";
export type LogFilter = "all" | "warning" | "error";

export interface LogEntry {
  source: string;
  level: LogLevel;
  at: number | null;
  stamp: string;
  approx: boolean;
  text: string;
  detail: string;
  count: number;
  hidden: boolean;
}

export const logSources: { id: string; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "bot", label: "机器人" },
  { id: "wechat", label: "微信会话" },
  { id: "sender", label: "发送组件" },
  { id: "web", label: "管理台" },
  { id: "recovery", label: "自动恢复" },
  { id: "supervisor", label: "主控" },
];

export const sourceLabel = (id: string) =>
  logSources.find((item) => item.id === id)?.label ?? id;

export const levelLabel: Record<string, string> = {
  DEBUG: "调试",
  INFO: "信息",
  WARNING: "警告",
  ERROR: "错误",
  CRITICAL: "严重",
};

export const levelTag = (level: string) =>
  level === "ERROR" || level === "CRITICAL" ? "danger" : level === "WARNING" ? "warning" : "info";

export const readErrorLabel: Record<string, string> = {
  missing: "该组件尚未生成日志文件。",
  denied: "管理台没有权限读取该日志文件。",
  unavailable: "日志文件暂时无法读取。",
};

export function logQuery(source: string, level: LogFilter, q: string, limit = 200) {
  const params = new URLSearchParams({ source, level, limit: String(limit) });
  const text = q.trim();
  if (text) params.set("q", text.slice(0, 200));
  return `/logs?${params}`;
}

/** Display time: the logged stamp, or the time inherited from the line before it. */
export function entryTime(entry: Pick<LogEntry, "at" | "stamp" | "approx">) {
  if (entry.stamp) return entry.stamp;
  if (entry.at == null) return "时间未知";
  const value = new Date(entry.at * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `≈ ${pad(value.getMonth() + 1)}-${pad(value.getDate())} ${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`;
}

export function entryCopy(entry: LogEntry) {
  const head = `${entryTime(entry)} ${entry.level} [${sourceLabel(entry.source)}] ${entry.text}`;
  const repeat = entry.count > 1 ? ` (×${entry.count})` : "";
  return entry.detail ? `${head}${repeat}\n${entry.detail}` : head + repeat;
}
