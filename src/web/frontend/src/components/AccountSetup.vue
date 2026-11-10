<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { ArrowRight, ArrowDown, ChatDotRound, CircleCheck, Connection, Iphone, Key, Loading, Refresh, VideoPlay } from '@element-plus/icons-vue';
import { api } from '../api';
import { LOGGED_IN, setupState, type StepId } from '../setupModel';
import { runServiceAction, runningOperation } from '../serviceActions';

const props = defineProps<{ snapshot: any; catalog: any }>();
const emit = defineEmits<{ navigate: [page: string]; create: []; refresh: []; reloadModels: []; error: [error: any] }>();
const dialog = ref(false), login = ref<any>(null), loginBusy = ref(false), submitting = ref(false);
const loginError = ref(''), expanded = ref(false);
const phase = computed(() => login.value?.phase || props.snapshot?.runtime?.login_phase || 'unknown');
const loggedIn = computed(() => LOGGED_IN.includes(phase.value));
const state = computed(() => setupState(props.snapshot, props.catalog, login.value?.phase));
const operation = computed(() => runningOperation(props.snapshot));
const busy = computed(() => submitting.value || !!operation.value);
const count = computed(() => props.snapshot?.instances?.length || 0);
const canControl = computed(() => !!props.snapshot?.capabilities?.service_control);
const icons: Record<StepId, any> = { keys: Key, login: Iphone, instances: ChatDotRound, launch: VideoPlay };
const labels: Record<string, string> = {
  logged_in: '微信已登录', setup_required: '微信已登录，等待连接', qr_required: '等待扫码',
  qr_expired: '二维码已过期', phone_confirmation: '等待手机确认', confirmation_pending: '等待手机确认',
  login_ready: '正在准备登录', open_ready: '正在进入微信', opening_wechat: '正在进入微信',
  client_stopped: '微信尚未运行', waiting_manual: '需要在微信端确认', completed: '正在核对登录状态',
  waiting: '正在连接微信', unknown: '等待获取登录状态',
};
const phaseLabel = computed(() => labels[phase.value] || '正在核对登录状态');
const summary = computed(() => {
  const done = state.value.steps.filter((step) => step.done).length;
  return state.value.next ? `已完成 ${done}/4 步` : `一切就绪 · ${count.value} 个实例已授权，机器人运行中`;
});

let timer: ReturnType<typeof setTimeout> | undefined;
let generation = 0, failures = 0, disposed = false;
function stopPolling() { generation++; if (timer) clearTimeout(timer); timer = undefined; }
function schedule() {
  if (disposed) return;
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { timer = undefined; if (dialog.value) void fetchLogin(); }, 4000);
}
async function fetchLogin() {
  if (loginBusy.value || !dialog.value) return;
  if (document.hidden) { schedule(); return; }
  const current = generation;
  loginBusy.value = true;
  try {
    const result = await api('/account/login', { method: 'POST', body: '{}' });
    if (current !== generation) return;
    login.value = result;
    loginError.value = ''; failures = 0;
    if (LOGGED_IN.includes(result.phase)) emit('refresh');
    else schedule();
  } catch (e: any) {
    if (current !== generation) return;
    login.value = null;
    loginError.value = e.message || '暂时无法获取二维码，请稍后重试。';
    if (e.status === 401) { dialog.value = false; emit('error', e); }
    else if (++failures < 3) schedule();
  } finally {
    loginBusy.value = false;
    if (current !== generation && dialog.value) schedule();
  }
}
function openLogin() { login.value = null; dialog.value = true; }
function retryLogin() { stopPolling(); failures = 0; void fetchLogin(); }
watch(dialog, (open) => {
  stopPolling(); failures = 0; loginError.value = '';
  if (open) void fetchLogin();
});
onBeforeUnmount(() => { disposed = true; stopPolling(); });

async function run(action: 'authorize' | 'authorize_start' | 'start') {
  submitting.value = true;
  try {
    if (await runServiceAction(action, count.value, (e) => emit('error', e))) emit('refresh');
  } finally { submitting.value = false; }
}
async function connectAccount() {
  dialog.value = false;
  await run('authorize');
}
</script>

<template>
  <section class="account-setup" aria-labelledby="account-setup-title">
    <div class="setup-heading">
      <div class="setup-title">
        <span class="setup-symbol"><el-icon><ChatDotRound /></el-icon></span>
        <div><h3 id="account-setup-title">{{ state.next ? '开始使用' : '微信账号' }}</h3><p>{{ summary }}</p></div>
      </div>
      <div class="setup-heading-actions">
        <span :class="['setup-status', { ready: loggedIn }]" role="status"><i></i>{{ phaseLabel }}</span>
        <el-button v-if="!state.next" text size="small" :aria-expanded="expanded" @click="expanded = !expanded">
          {{ expanded ? '收起' : '查看步骤' }}<el-icon :class="['fold-icon', { open: expanded }]"><ArrowDown /></el-icon>
        </el-button>
      </div>
    </div>

    <div v-if="operation" class="setup-progress" role="status">
      <el-icon class="is-loading"><Loading /></el-icon><span>{{ operation.message }}</span>
    </div>

    <ol v-if="state.next || expanded" class="setup-steps">
      <li v-for="(step, index) in state.steps" :key="step.id"
          :class="['setup-step', { complete: step.done, current: state.next === step.id }]">
        <div class="step-kicker">
          <span class="step-number"><el-icon v-if="step.done"><CircleCheck /></el-icon><template v-else>{{ index + 1 }}</template></span>
          <el-icon class="step-icon"><component :is="icons[step.id]" /></el-icon>
        </div>
        <h4>{{ step.title }}</h4>
        <p>{{ step.detail }}</p>
        <div class="step-actions">
          <template v-if="step.id === 'keys'">
            <el-button v-if="!step.done" :type="state.next === 'keys' ? 'primary' : 'default'" :icon="Refresh" @click="emit('reloadModels')">我已填写，重新检测</el-button>
          </template>
          <template v-else-if="step.id === 'login'">
            <el-button v-if="!state.loggedIn" :type="state.next === 'login' ? 'primary' : 'default'" :icon="Iphone" @click="openLogin">打开登录二维码</el-button>
            <el-button v-else-if="!state.bound" :type="state.next === 'login' ? 'primary' : 'default'" :icon="Connection"
              :disabled="busy || !canControl" :loading="submitting" @click="connectAccount">连接账号</el-button>
            <el-button v-else :icon="Refresh" @click="openLogin">检查登录状态</el-button>
          </template>
          <template v-else-if="step.id === 'instances'">
            <el-button :type="state.next === 'instances' ? 'primary' : 'default'" :icon="ChatDotRound"
              @click="count ? emit('navigate', 'instances') : emit('create')">{{ count ? '管理会话实例' : '创建会话实例' }}</el-button>
          </template>
          <template v-else>
            <el-button v-if="state.launch" :type="state.next === 'launch' ? 'primary' : 'default'" :icon="VideoPlay"
              :disabled="busy || !canControl || !state.loggedIn" :loading="submitting" @click="run(state.launch!)">
              {{ state.launch === 'start' ? '启动机器人' : state.launch === 'authorize' ? '授权并重启机器人' : '授权并启动机器人' }}</el-button>
            <el-button v-else-if="step.done" :icon="ArrowRight" @click="emit('navigate', 'services')">服务管理</el-button>
          </template>
        </div>
      </li>
    </ol>
    <div v-if="state.next || expanded" class="setup-footer"><span>新增实例后回到这里一键授权；机器人运行时会短暂重启。</span></div>
  </section>

  <el-dialog v-model="dialog" title="连接微信" width="min(440px, calc(100vw - 32px))" align-center class="account-login-dialog" :close-on-click-modal="false" @closed="!dialog && (login = null)">
    <div class="login-viewer" aria-live="polite">
      <div v-if="loggedIn && !loginBusy && login" class="login-success"><el-icon><CircleCheck /></el-icon><h3>微信登录成功</h3>
        <p>{{ state.bound ? '账号已连接，可以继续配置实例。' : '下一步：连接账号，之后即可搜索联系人和群聊。' }}</p></div>
      <template v-else>
        <div class="qr-frame" v-loading="loginBusy && !login?.qr">
          <img v-if="login?.qr && !loginError" :src="login.qr" alt="使用手机微信扫描此二维码登录" />
          <div v-else class="qr-placeholder"><el-icon><Iphone /></el-icon><span>{{ loginBusy ? '正在获取登录状态' : phaseLabel }}</span></div>
        </div>
        <h3>{{ phaseLabel }}</h3>
        <p>{{ ['phone_confirmation', 'confirmation_pending'].includes(phase) ? '请打开手机微信，确认本次登录。' : '二维码和登录状态每 4 秒自动更新，扫码后请在手机确认。' }}</p>
      </template>
      <el-alert v-if="loginError" :title="loginError" type="warning" show-icon :closable="false" />
    </div>
    <template #footer>
      <el-button @click="dialog = false">{{ loggedIn ? '关闭' : '稍后登录' }}</el-button>
      <el-button v-if="loggedIn && !state.bound" type="primary" :icon="Connection" :disabled="busy || !canControl" @click="connectAccount">连接账号</el-button>
      <el-button v-else :icon="Refresh" :loading="loginBusy" @click="retryLogin">刷新状态</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.account-setup { margin-bottom: 20px; padding: 24px; border: 1px solid var(--ui-border); border-radius: 12px; background: var(--ui-surface); box-shadow: var(--ui-shadow); }
.setup-heading,.setup-title,.setup-footer,.setup-heading-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.setup-title,.setup-heading-actions { justify-content: flex-start; }
.setup-heading-actions { gap: 8px; }
.setup-title h3 { font-size: 18px; font-weight: 650; letter-spacing: .3px; }
.setup-title p { margin-top: 5px; font-size: 13px; color: var(--ui-muted); }
.setup-symbol { display: grid; place-items: center; flex: 0 0 44px; height: 44px; border-radius: 12px; background: var(--ui-accent-soft); color: var(--ui-accent); font-size: 24px; }
.setup-status { display: inline-flex; align-items: center; gap: 7px; padding: 7px 11px; background: var(--ui-warning-soft); border: 1px solid var(--ui-warning-border); border-radius: 20px; color: #b45309; font-size: 12px; white-space: nowrap; }
.setup-status i { width: 6px; height: 6px; background: currentColor; border-radius: 50%; }
.setup-status.ready { color: #059669; background: var(--ui-success-soft); border-color: var(--ui-success-border); }
.fold-icon { margin-left: 4px; transition: transform .15s ease; }
.fold-icon.open { transform: rotate(180deg); }
.setup-progress { display: flex; align-items: center; gap: 10px; margin-top: 16px; padding: 11px 14px; border: 1px solid var(--ui-accent-border); border-radius: 10px; background: var(--ui-accent-soft); color: var(--ui-accent); font-size: 13px; }
.setup-steps { list-style: none; display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 0; margin: 22px 0 18px; padding: 0; border: 1px solid var(--ui-border); border-radius: 12px; overflow: hidden; }
.setup-step { padding: 19px 20px; min-width: 0; border-right: 1px solid var(--ui-border); background: var(--ui-surface); transition: background .15s ease; display: flex; flex-direction: column; align-items: flex-start; }
.setup-step:last-child { border-right: 0; }
.setup-step.current { background: var(--ui-accent-soft); box-shadow: inset 0 3px 0 var(--ui-accent); }
.step-kicker { display: flex; align-items: center; gap: 8px; color: var(--ui-muted); font-size: 12px; }
.step-icon { font-size: 16px; }
.step-number { display: grid; place-items: center; width: 25px; height: 25px; border: 1px solid var(--ui-border); border-radius: 50%; font-size: 11px; font-weight: 600; background: var(--ui-surface); }
.current .step-number { color: var(--ui-accent); border-color: var(--ui-accent); }
.complete .step-number { color: #059669; background: var(--ui-success-soft); border-color: var(--ui-success-border); font-size: 17px; }
.setup-step h4 { margin: 13px 0 7px; font-size: 15px; font-weight: 600; }
.setup-step p { color: var(--ui-secondary); font-size: 13px; line-height: 1.65; margin-bottom: 16px; overflow-wrap: anywhere; }
.step-actions { margin-top: auto; }
.setup-footer { color: var(--ui-muted); font-size: 12px; line-height: 1.6; }
.login-viewer { text-align: center; }
.qr-frame { width: 248px; height: 248px; margin: 4px auto 22px; padding: 12px; background: var(--ui-surface); border: 1px solid var(--ui-border); border-radius: 12px; box-shadow: var(--ui-shadow-sm); }
.qr-frame img { width: 100%; height: 100%; object-fit: contain; }
.qr-placeholder { height: 100%; display: flex; flex-direction: column; justify-content: center; gap: 16px; align-items: center; color: var(--ui-muted); font-size: 12px; background: var(--ui-surface-soft); border-radius: 8px; }
.qr-placeholder > .el-icon { font-size: 40px; }
.login-viewer h3 { font-size: 18px; }
.login-viewer p { margin: 10px 10px 20px; color: var(--ui-muted); line-height: 1.8; font-size: 13px; }
.login-success { padding: 25px 0 10px; }
.login-success > .el-icon { color: var(--ui-success); font-size: 64px; margin-bottom: 20px; }
@media (max-width: 1150px) { .setup-steps { grid-template-columns: repeat(2,minmax(0,1fr)); } .setup-step:nth-child(2) { border-right: 0; } .setup-step:nth-child(-n+2) { border-bottom: 1px solid var(--ui-border); } }
@media (max-width: 900px) { .account-setup { padding: 18px; } .setup-step { padding: 16px; } }
@media (max-width: 640px) { .setup-heading { align-items: flex-start; flex-direction: column; } .setup-steps { grid-template-columns: 1fr; margin-top: 18px; } .setup-step { border-right: 0; border-bottom: 1px solid var(--ui-border); } .setup-step:last-child { border-bottom: 0; } .setup-step p { margin-bottom: 14px; } .setup-footer { align-items: flex-start; flex-direction: column; gap: 10px; } }
</style>
