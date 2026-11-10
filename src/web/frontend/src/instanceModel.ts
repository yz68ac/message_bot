// One status per instance row, combining the saved switch, runtime state,
// send authorization and unapplied edits into the thing the owner should know first.
export type StatusTone = "ok" | "warn" | "error" | "idle";

export interface InstanceStatus {
  label: string;
  tone: StatusTone;
  detail: string;
}

export const instanceErrors: Record<string, string> = {
  instance_target_not_allowed: "会话尚未获得发送授权，请授权后重试。",
  instance_configuration_changed: "配置在操作期间发生变化，请重新应用。",
  account_config_changed_restart_required: "账号配置发生变化，需要重启账号服务。",
  instance_chat_identity_mismatch: "会话身份核对失败，请确认当前登录的微信账号拥有此会话。",
  instance_transition_failed: "实例切换失败，请检查账号登录、模型配置和运行日志。",
};

export function errorText(code: string) {
  return instanceErrors[code] || `实例操作失败（${code}），请查看运行日志。`;
}

/** Blocked reasons come from the delivery journal as "<code>:<detail>". */
export function blockedText(reason: string) {
  if (!reason) return "";
  if (reason.startsWith("send_confirmation_timeout"))
    return "发出后未在微信中找到对应记录，已暂停发送（仍在接收消息）。确认微信正常后会自动恢复。";
  if (reason.startsWith("native_text_limit"))
    return "回复超过微信单条消息上限，已暂停发送。可调低“每条发送消息字符上限”。";
  return reason;
}

export function instanceStatus(row: any, pendingAuthorization: string[] = [], requested = false): InstanceStatus {
  if (row.error) return { label: "出错", tone: "error", detail: errorText(row.error) };
  if (requested) return { label: "正在处理", tone: "warn", detail: "等待机器人确认实例操作。" };
  if (row.enabled && pendingAuthorization.includes(row.id))
    return { label: "待授权", tone: "warn", detail: "会话尚未获得发送授权，授权后才能回复。" };
  switch (row.runtime_state) {
    case "stopping":
      return { label: "正在停止", tone: "warn", detail: "等待进行中的请求结束。" };
    case "running":
      return row.config_pending
        ? { label: "有未应用的修改", tone: "warn", detail: "运行中使用的是旧配置，点击“应用”生效。" }
        : { label: "运行中", tone: "ok", detail: row.blocked_reason ? blockedText(row.blocked_reason) : "" };
    case "waiting_service":
      return { label: "等待机器人启动", tone: "idle", detail: "已启用，机器人启动后开始回复。" };
  }
  if (!row.enabled) return { label: "已停用", tone: "idle", detail: "" };
  if (row.runtime_state === "stopped")
    return { label: "未运行", tone: "warn", detail: row.config_pending ? "已启用但尚未应用，点击“应用”生效。" : "已启用但当前未运行。" };
  return { label: "状态未知", tone: "idle", detail: "暂时读不到机器人的运行快照。" };
}
