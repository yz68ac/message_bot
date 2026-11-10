import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "./api";

export type ServiceAction = "start" | "stop" | "apply" | "authorize" | "authorize_start";

const copy: Record<ServiceAction, [title: string, body: (count: number) => string, type: "info" | "warning"]> = {
  start: ["启动机器人", () => "将加载已保存的配置，开始监听并自动回复。", "info"],
  stop: ["停止机器人", () => "将停止机器人及自动恢复。微信登录会话保留，未完成队列由机器人关闭流程保存。", "warning"],
  apply: ["应用全部配置", () => "所有运行实例会随账号服务受控重启。也可在实例页单独应用某个实例。账号服务已停止时，不会自动启动。", "info"],
  authorize: ["授权实例会话", (count) => count
    ? `机器人会短暂停止（等待进行中的发送完成），核对当前微信账号并为 ${count} 个实例授权发送，然后自动重新启动。请确认当前账号拥有这些群或联系人。`
    : "将当前登录的微信账号绑定到此运行环境，之后即可搜索联系人和群聊。", "info"],
  authorize_start: ["授权并启动", (count) =>
    `核对当前微信账号，为 ${count} 个实例授权发送，然后启动机器人。请确认当前账号拥有这些群或联系人。`, "info"],
};

/** Confirm, submit a background service operation, and report it. Returns false when cancelled or failed. */
export async function runServiceAction(action: ServiceAction, instanceCount: number, fail: (error: any) => void) {
  const [title, body, type] = copy[action];
  try {
    await ElMessageBox.confirm(body(instanceCount), title, {
      confirmButtonText: "确认", cancelButtonText: "取消", type,
    });
  } catch {
    return false;
  }
  try {
    await api(`/services/${action}`, { method: "POST", body: "{}" });
    ElMessage.success(action.startsWith("authorize")
      ? "已开始授权，进度显示在工作台和操作记录中。"
      : "操作已提交，可在服务管理中查看结果。");
    return true;
  } catch (e) {
    fail(e);
    return false;
  }
}

export function runningOperation(snapshot: any) {
  return (snapshot?.operations || []).find((op: any) => op.state === "running");
}
