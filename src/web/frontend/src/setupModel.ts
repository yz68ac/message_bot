// First-run and "add a chat" guide: which steps are done and which one comes next.
export type StepId = "keys" | "login" | "instances" | "launch";
export type SetupAction = "authorize" | "authorize_start" | "start" | null;

export interface SetupStep {
  id: StepId;
  done: boolean;
  title: string;
  detail: string;
}

export interface SetupState {
  steps: SetupStep[];
  next: StepId | null;
  /** What the final step's button does now. */
  launch: SetupAction;
  loggedIn: boolean;
  bound: boolean;
  running: boolean;
  pending: string[];
}

export const LOGGED_IN = ["logged_in", "setup_required"];

export function botRunning(snapshot: any) {
  return ["active", "activating"].includes(snapshot?.services?.bot?.state);
}

/** Stop → bind → restore is one backend operation; this picks the variant for the current state. */
export function authorizeAction(snapshot: any): "authorize" | "authorize_start" {
  return botRunning(snapshot) ? "authorize" : "authorize_start";
}

export function setupState(snapshot: any, catalog: any, loginPhase?: string): SetupState {
  const setup = snapshot?.account_setup || {};
  const instances: any[] = snapshot?.instances || [];
  const phase = loginPhase || snapshot?.runtime?.login_phase || "unknown";
  const loggedIn = LOGGED_IN.includes(phase);
  const bound = !!setup.bound;
  const running = botRunning(snapshot);
  const pending: string[] = setup.pending_instances || [];
  const providers: any[] = catalog?.providers || [];
  const keyed = providers.filter((item) => item.configured).map((item) => item.id);
  const enabled = instances.filter((item) => item.enabled).length;
  const authorized = bound && instances.length > 0 && pending.length === 0;

  let launch: SetupAction = null;
  if (instances.length && pending.length) launch = authorizeAction(snapshot);
  else if (authorized && !running) launch = "start";

  const steps: SetupStep[] = [
    {
      id: "keys",
      done: keyed.length > 0,
      title: keyed.length ? "模型密钥已填写" : "填写模型密钥",
      detail: keyed.length
        ? `已配置：${keyed.join("、")}。`
        : "在部署目录的 provider.env 中填写所用服务的密钥，例如 DEEPSEEK_API_KEY。保存后刷新本页即可识别。",
    },
    {
      id: "login",
      done: loggedIn && bound,
      title: !loggedIn ? "扫码登录微信" : bound ? "微信已连接" : "连接当前微信账号",
      detail: !loggedIn
        ? "使用手机微信扫码，并在手机上确认登录。"
        : bound
          ? "登录状态保留在账号目录中，重启容器后无需重新扫码。"
          : "登录成功。连接账号后即可搜索联系人和群聊来创建实例。",
    },
    {
      id: "instances",
      done: instances.length > 0,
      title: instances.length ? `已配置 ${instances.length} 个实例` : "为会话创建实例",
      detail: instances.length
        ? `${enabled} 个已启用。每个实例独立设置模型、提示词和记忆。`
        : "选择一个群聊或联系人，配置回复模型。可以先用“文件传输助手”试一试。",
    },
    {
      id: "launch",
      done: authorized && running,
      title: authorized && running
        ? "机器人运行中"
        : pending.length
          ? `${pending.length} 个实例待授权`
          : instances.length
            ? "启动机器人"
            : "授权并启动",
      detail: authorized && running
        ? "所有实例会话均已授权。新增实例后在这里一键授权。"
        : pending.length
          ? `${pending.join("、")} 尚未获得发送授权。${running ? "授权时机器人会短暂重启。" : "授权后机器人会自动启动。"}`
          : instances.length
            ? "所有实例会话均已授权，启动后开始自动回复。"
            : "创建实例后，在这里授权会话并启动机器人。",
    },
  ];
  const next = steps.find((step) => !step.done)?.id ?? null;
  return { steps, next, launch, loggedIn, bound, running, pending };
}
