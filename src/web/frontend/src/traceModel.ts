export type TraceRole = "reply" | "participation" | "memory" | "avatar" | "group_profile";

export interface TraceRef {
  instance: string;
  role: TraceRole;
  task: number;
}

export interface TraceRow {
  id: number;
  ts: number;
  run_id: string;
  instance: string;
  role: TraceRole;
  task: number | null;
  version: number | null;
  attempt: number | null;
  ok: number;
  error: string;
  model: string;
  elapsed_ms: number;
  tokens_in: number;
  tokens_out: number;
  finish: string;
  calls_n: number;
  summary: string;
}

export interface TraceFilters {
  instance: string;
  role: string;
  problems: boolean;
  q: string;
  task: number | null;
  run: string;
}

/** What the URL hash can carry: the open record plus the filters a link wants to preset. */
export interface TraceRoute {
  id?: number;
  instance?: string;
  role?: string;
  task?: number;
  run?: string;
  problems?: boolean;
}

export const roleLabel: Record<string, string> = {
  reply: "回复",
  participation: "接话判断",
  memory: "记忆整理",
  avatar: "头像分类",
  group_profile: "群资料",
};

export const roleOptions = Object.entries(roleLabel).map(([value, label]) => ({ value, label }));

export const emptyFilters = (): TraceFilters => ({ instance: "", role: "", problems: false, q: "", task: null, run: "" });

export function filtersOfRoute(route: TraceRoute): TraceFilters {
  return {
    ...emptyFilters(),
    instance: route.instance ?? "",
    role: route.role ?? "",
    task: route.task ?? null,
    run: route.run ?? "",
    problems: !!route.problems,
  };
}

/** The part of a route that changes the list (not which record is open). */
export const listKey = (route: TraceRoute) =>
  JSON.stringify([route.instance ?? "", route.role ?? "", route.task ?? null, route.run ?? "", !!route.problems]);

export function traceQuery(filters: TraceFilters, before: number | null = null, limit = 100) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (filters.instance) params.set("instance", filters.instance);
  if (filters.role) params.set("role", filters.role);
  if (filters.task != null) params.set("task", String(filters.task));
  if (filters.run) params.set("run", filters.run);
  if (filters.problems) params.set("problems", "true");
  const text = filters.q.trim();
  if (text) params.set("q", text.slice(0, 200));
  if (before != null) params.set("before", String(before));
  return `/trace?${params}`;
}

export function locateQuery(ref: TraceRef, at: number | null | undefined) {
  const params = new URLSearchParams({ instance: ref.instance, role: ref.role, task: String(ref.task) });
  if (at != null) params.set("at", String(at));
  return `/trace/locate?${params}`;
}

// ---- the page and the trace route live in the URL hash: #/trace?id=2&instance=alpha

export function pageOfHash(hash: string) {
  const body = hash.replace(/^#\/?/, "");
  const cut = body.indexOf("?");
  return cut < 0 ? body : body.slice(0, cut);
}

const INSTANCE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$/;
const RUN = /^[0-9A-Za-z-]{1,40}$/;
const NUMBER = /^\d{1,12}$/;

export function traceRouteOfHash(hash: string): TraceRoute {
  const cut = hash.indexOf("?");
  if (cut < 0) return {};
  const params = new URLSearchParams(hash.slice(cut + 1));
  const route: TraceRoute = {};
  const id = params.get("id"),
    task = params.get("task"),
    instance = params.get("instance"),
    role = params.get("role"),
    run = params.get("run");
  if (id && NUMBER.test(id)) route.id = Number(id);
  if (task && NUMBER.test(task)) route.task = Number(task);
  if (instance && INSTANCE.test(instance)) route.instance = instance;
  if (role && role in roleLabel) route.role = role;
  if (run && RUN.test(run)) route.run = run;
  if (params.get("problems") === "true") route.problems = true;
  return route;
}

export function traceHash(route: TraceRoute) {
  const params = new URLSearchParams();
  if (route.id != null) params.set("id", String(route.id));
  if (route.instance) params.set("instance", route.instance);
  if (route.role) params.set("role", route.role);
  if (route.task != null) params.set("task", String(route.task));
  if (route.run) params.set("run", route.run);
  if (route.problems) params.set("problems", "true");
  const text = params.toString();
  return text ? `#/trace?${text}` : "#/trace";
}

// ---- labels

export const errorLabel: Record<string, string> = {
  empty_reply: "模型返回了空回复",
  output_limit: "输出被长度上限截断",
  output_filtered: "被内容过滤拦截",
  incomplete_response: "回复不完整",
  invalid_tool_calls: "工具调用格式无效",
  deadline_exceeded: "超时",
  CancelledError: "已取消（被新版本替代）",
  missing_api_key: "缺少模型密钥",
  worker_timeout: "生成超时",
};

export function errorText(code: string) {
  if (!code) return "";
  return errorLabel[code] ? `${errorLabel[code]}（${code}）` : code;
}

const CUT_OFF = new Set(["length", "content_filter", "pause_turn", "incomplete_response", "invalid_tool_calls"]);

export function outcome(row: Pick<TraceRow, "ok" | "error" | "finish">): { label: string; tag: "success" | "danger" | "warning" } {
  if (!row.ok) return { label: errorText(row.error) || "失败", tag: "danger" };
  if (CUT_OFF.has(row.finish)) return { label: `结束原因 ${row.finish}`, tag: "warning" };
  return { label: "成功", tag: "success" };
}

const pad = (n: number) => String(n).padStart(2, "0");

export function stampOf(ts: number) {
  const value = new Date(ts * 1000);
  return `${pad(value.getMonth() + 1)}-${pad(value.getDate())} ${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`;
}

export const clockOf = (ts: number) => stampOf(ts).slice(6);

export function durationLabel(ms: number) {
  if (!(ms >= 0)) return "-";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function tokenLabel(row: Pick<TraceRow, "tokens_in" | "tokens_out">) {
  return row.tokens_in || row.tokens_out ? `${row.tokens_in} → ${row.tokens_out} tokens` : "无用量";
}

// ---- chat payloads: the user message of a reply request is one compact JSON document

export interface ChatMessage {
  id: number;
  time?: number;
  sender?: string;
  text?: string;
  [key: string]: unknown;
}

export interface ChatPayload {
  messages: ChatMessage[];
  source_ids: number[];
  members: Record<string, { alias?: string }>;
  memory: string[];
  rest: Record<string, unknown>;
}

export function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part: any) =>
      part?.type === "text" ? String(part.text ?? "") : part?.type === "image_url" ? String(part.image_url?.url ?? "[图片]") : "",
    )
    .filter(Boolean)
    .join("\n");
}

export function parsePayload(content: unknown): ChatPayload | null {
  const candidates =
    typeof content === "string"
      ? [content]
      : Array.isArray(content)
        ? content.filter((part: any) => part?.type === "text").map((part: any) => String(part.text ?? ""))
        : [];
  for (const text of candidates) {
    let value: any;
    try {
      value = JSON.parse(text);
    } catch {
      continue;
    }
    if (!value || !Array.isArray(value.messages) || !Array.isArray(value.source_ids)) continue;
    const { messages, source_ids, members, memory, ...rest } = value;
    return {
      messages,
      source_ids,
      members: members && typeof members === "object" ? members : {},
      memory: Array.isArray(memory) ? memory.map(String) : [],
      rest,
    };
  }
  return null;
}

export function senderLabel(payload: Pick<ChatPayload, "members">, sender: unknown) {
  const id = String(sender ?? "");
  const alias = payload.members[id]?.alias;
  return alias || (id.length > 18 ? `${id.slice(0, 18)}…` : id) || "未知";
}

export const isFresh = (payload: Pick<ChatPayload, "source_ids">, message: ChatMessage) =>
  payload.source_ids.includes(message.id);

// ---- rounds: every tool round resends the whole conversation, so show only what is new

export function sharedPrefix(previous: unknown[], current: unknown[]) {
  let count = 0;
  while (count < previous.length && count < current.length && JSON.stringify(previous[count]) === JSON.stringify(current[count])) count++;
  return count;
}

export function newMessages(calls: { request?: { messages?: unknown[] } }[], index: number) {
  const messages = calls[index]?.request?.messages ?? [];
  const skipped = index === 0 ? 0 : sharedPrefix(calls[index - 1]?.request?.messages ?? [], messages);
  return { skipped, messages: messages.slice(skipped) };
}

export function prettyText(value: unknown) {
  if (typeof value !== "string") return JSON.stringify(value, null, 2) ?? "";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

export const traceCopy = (detail: unknown) => JSON.stringify(detail, null, 2);
