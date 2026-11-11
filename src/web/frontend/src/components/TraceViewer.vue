<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { Refresh } from "@element-plus/icons-vue";
import { api } from "../api";
import TraceMessage from "./TraceMessage.vue";
import {
  durationLabel,
  errorText,
  filtersOfRoute,
  listKey,
  newMessages,
  outcome,
  prettyText,
  roleLabel,
  roleOptions,
  stampOf,
  tokenLabel,
  traceCopy,
  traceQuery,
  type TraceFilters,
  type TraceRoute,
  type TraceRow,
} from "../traceModel";

const props = defineProps<{ route: TraceRoute }>();
const emit = defineEmits<{ open: [id: number | null]; error: [error: any] }>();

const PAGE = 100;
const filters = ref<TraceFilters>(filtersOfRoute(props.route));
const rows = ref<TraceRow[]>([]),
  total = ref(0),
  instances = ref<string[]>([]),
  unavailable = ref(""),
  busy = ref(false),
  error = ref(""),
  follow = ref(true);
let sequence = 0,
  timer: ReturnType<typeof setInterval> | undefined,
  debounce: ReturnType<typeof setTimeout> | undefined;

const unavailableText: Record<string, string> = {
  missing: "还没有追踪记录：机器人尚未处理过消息，或实例关闭了“保存提示词与模型回复”。",
  denied: "管理台没有权限读取追踪文件。",
  unavailable: "追踪文件暂时无法读取，稍后再试。",
};

async function load(options: { quiet?: boolean; append?: boolean } = {}) {
  const current = ++sequence;
  if (!options.quiet) busy.value = true;
  try {
    const before = options.append && rows.value.length ? rows.value[rows.value.length - 1].id : null;
    const data = await api(traceQuery(filters.value, before, PAGE));
    if (current !== sequence) return; // A newer filter already replaced this request.
    rows.value = options.append ? [...rows.value, ...data.rows] : data.rows;
    total.value = data.total;
    instances.value = data.instances;
    unavailable.value = data.available ? "" : data.error || "unavailable";
    error.value = "";
  } catch (e: any) {
    if (current !== sequence) return;
    error.value = e.message;
    if (e.status === 401) emit("error", e);
  } finally {
    if (current === sequence) busy.value = false;
  }
}

// ---- detail drawer
const selected = ref<number | null>(props.route.id ?? null);
const detail = ref<any>(null),
  detailBusy = ref(false),
  detailError = ref("");
const showAll = ref<Record<number, boolean>>({});
let detailSequence = 0;

async function loadDetail(id: number) {
  const current = ++detailSequence;
  detailBusy.value = true;
  detailError.value = "";
  showAll.value = {};
  try {
    const data = await api(`/trace/${id}`);
    if (current !== detailSequence) return;
    detail.value = data;
    if (!data.available) detailError.value = unavailableText[data.error] || unavailableText.unavailable;
  } catch (e: any) {
    if (current !== detailSequence) return;
    detail.value = null;
    detailError.value = e.message;
    if (e.status === 401) emit("error", e);
  } finally {
    if (current === detailSequence) detailBusy.value = false;
  }
}

function openDetail(id: number) {
  selected.value = id;
  emit("open", id);
  void loadDetail(id);
}

function closeDetail() {
  detailSequence++;
  selected.value = null;
  detail.value = null;
  emit("open", null);
}

const drawer = computed({
  get: () => selected.value !== null,
  set: (open: boolean) => {
    if (!open) closeDetail();
  },
});
const job = computed(() => detail.value?.job);
const rounds = computed(() =>
  (job.value?.calls ?? []).map((call: any, index: number) => ({ call, index, ...newMessages(job.value.calls, index) })),
);
const drawerTitle = computed(() =>
  job.value
    ? `${roleLabel[job.value.role] || job.value.role}${job.value.task != null ? ` #${job.value.task}` : ""}${job.value.attempt > 1 ? ` · 第 ${job.value.attempt} 次` : ""}`
    : "追踪详情",
);

const eventTitle: Record<string, string> = {
  reply_final: "实际排队发送的内容",
  reply_failed: "生成失败，改发的兜底回复",
  reply_discarded: "生成被废弃（消息已被新版本替代）",
  participation_decision: "接话判断结果",
  participation_failed: "接话判断失败",
};

async function copy() {
  try {
    await navigator.clipboard.writeText(traceCopy(detail.value));
    ElMessage.success("已复制这条追踪的完整 JSON。");
  } catch {
    ElMessage.error("浏览器不允许复制，请手动选中文本。");
  }
}

// ---- reacting to the route: a link from the log page, or back/forward
watch(
  () => props.route,
  (route, previous) => {
    if (listKey(route) !== listKey(previous)) {
      filters.value = filtersOfRoute(route);
      void load();
    }
    const id = route.id ?? null;
    if (id !== selected.value) {
      selected.value = id;
      if (id === null) {
        detailSequence++;
        detail.value = null;
      } else void loadDetail(id);
    }
  },
);
watch(
  () => [filters.value.instance, filters.value.role, filters.value.problems, filters.value.task],
  () => void load(),
);
watch(
  () => filters.value.q,
  () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => void load(), 300);
  },
);
onMounted(() => {
  void load();
  if (selected.value !== null) void loadDetail(selected.value);
  timer = setInterval(() => {
    // Paging further back would be undone by a refresh, and an open record should not move under the reader.
    if (follow.value && !busy.value && selected.value === null && rows.value.length <= PAGE && document.visibilityState === "visible")
      void load({ quiet: true });
  }, 8000);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  clearTimeout(debounce);
});
defineExpose({ load });
</script>

<template>
  <section class="panel trace-panel">
    <div class="logs-toolbar">
      <div class="logs-controls">
        <el-select v-model="filters.instance" size="small" class="trace-select" placeholder="全部实例" clearable :value-on-clear="''" aria-label="实例">
          <el-option v-for="name in instances" :key="name" :value="name" :label="name" />
        </el-select>
        <el-select v-model="filters.role" size="small" class="trace-select" placeholder="全部类型" clearable :value-on-clear="''" aria-label="类型">
          <el-option v-for="item in roleOptions" :key="item.value" :value="item.value" :label="item.label" />
        </el-select>
        <el-switch v-model="filters.problems" size="small" active-text="只看有问题的" />
        <el-tag v-if="filters.task !== null" size="small" closable effect="plain" @close="filters.task = null"
          >任务 #{{ filters.task }}</el-tag
        >
      </div>
      <div class="logs-controls">
        <el-input v-model="filters.q" size="small" clearable placeholder="搜索问题、回复或错误" class="logs-search" />
        <el-switch v-model="follow" size="small" active-text="自动刷新" />
        <el-button size="small" :icon="Refresh" :loading="busy" @click="load()">刷新</el-button>
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="logs-alert" />
    <el-alert v-if="unavailable" :title="unavailableText[unavailable]" :type="unavailable === 'missing' ? 'info' : 'warning'" :closable="false" class="logs-alert" />

    <div v-if="!rows.length && !busy && !unavailable" class="empty-state">
      {{ filters.problems ? "没有符合条件的问题记录。" : "没有符合条件的追踪记录。" }}
    </div>
    <ol v-else class="trace-list">
      <li v-for="row in rows" :key="row.id">
        <button type="button" :class="['trace-row', { bad: !row.ok, selected: row.id === selected }]" @click="openDetail(row.id)">
          <time>{{ stampOf(row.ts) }}</time>
          <el-tag size="small" effect="plain" disable-transitions>{{ roleLabel[row.role] || row.role }}</el-tag>
          <span class="trace-instance">{{ row.instance }}</span>
          <span v-if="row.task != null" class="trace-task"
            >#{{ row.task }}<template v-if="row.attempt && row.attempt > 1"> · 第 {{ row.attempt }} 次</template></span
          >
          <el-tag size="small" :type="outcome(row).tag" effect="light" disable-transitions>{{ outcome(row).label }}</el-tag>
          <span class="trace-meta">{{ row.model }} · {{ durationLabel(row.elapsed_ms) }} · {{ tokenLabel(row) }}</span>
          <span class="trace-summary">{{ row.summary || "（无摘要）" }}</span>
        </button>
      </li>
    </ol>
    <div v-if="rows.length < total" class="trace-more">
      <el-button size="small" :loading="busy" @click="load({ append: true })">加载更早的 {{ Math.min(PAGE, total - rows.length) }} 条</el-button>
    </div>
    <div class="logs-footnote">
      <span>显示 {{ rows.length }} / {{ total }} 条 · 保留最近 14 天，总量上限 300 MB</span>
      <span>开发阶段：这里按原样保存提示词（含聊天记录和记忆）与模型回复；正式使用前请关闭“保存提示词与模型回复”并删除 data/home/trace。</span>
    </div>
  </section>

  <el-drawer v-model="drawer" direction="rtl" size="min(980px, 100%)" :title="drawerTitle" append-to-body class="trace-drawer">
    <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" />
    <div v-else-if="detailBusy && !job" class="empty-state">正在读取…</div>
    <div v-else-if="job" class="trace-detail">
      <div class="trace-toolbar">
        <div v-if="detail.related.length > 1" class="trace-attempts">
          同一任务的其他尝试：
          <button v-for="item in detail.related" :key="item.id" type="button" :class="['trace-chip', { current: item.id === job.id, bad: !item.ok }]" @click="openDetail(item.id)">
            第 {{ item.attempt ?? "?" }} 次 · {{ item.ok ? "成功" : "失败" }}
          </button>
        </div>
        <el-button size="small" @click="copy">复制完整 JSON</el-button>
      </div>

      <dl class="trace-facts">
        <div><dt>时间</dt><dd>{{ stampOf(job.ts) }}</dd></div>
        <div><dt>实例</dt><dd>{{ job.instance }}</dd></div>
        <div><dt>任务</dt><dd>{{ job.task != null ? `#${job.task}（版本 ${job.version ?? "-"}）` : "-" }}</dd></div>
        <div><dt>模型</dt><dd>{{ job.model || "-" }}</dd></div>
        <div><dt>结果</dt><dd><el-tag size="small" :type="outcome(job).tag" effect="light" disable-transitions>{{ outcome(job).label }}</el-tag></dd></div>
        <div><dt>耗时</dt><dd>{{ durationLabel(job.elapsed_ms) }}</dd></div>
        <div><dt>用量</dt><dd>{{ tokenLabel(job) }}</dd></div>
        <div><dt>结束原因</dt><dd>{{ job.finish || "-" }}</dd></div>
        <div><dt>运行编号</dt><dd><code>{{ job.run_id }}</code></dd></div>
      </dl>
      <p v-if="job.context" class="trace-sub">
        上下文：{{ job.context.chars }} 字符 · 选入 {{ job.context.selected }} 条 · 保留历史 {{ job.context.retained }} 条 · 裁剪 {{ job.context.omitted }} 条
      </p>
      <p v-if="detail.job.truncated" class="trace-sub warn">内容过长，只显示前面的 {{ job.calls.length }} 轮。</p>

      <section v-if="job.error" class="trace-block bad">
        <h3>失败原因</h3>
        <p>{{ errorText(job.error) }}</p>
        <details v-if="job.traceback" class="trace-fold" open>
          <summary>堆栈</summary>
          <pre>{{ job.traceback }}</pre>
        </details>
      </section>

      <section v-if="job.result" class="trace-block">
        <h3>{{ job.role === "reply" ? "模型的最终回复（格式化前）" : "模型结果" }}</h3>
        <pre class="trace-text">{{ job.result }}</pre>
      </section>

      <section v-if="detail.events.length" class="trace-block">
        <h3>这个任务的后续事件<small>（含其他次尝试）</small></h3>
        <div v-for="item in detail.events" :key="item.id" class="trace-event">
          <div class="trace-msg-head">
            <span class="trace-role">{{ eventTitle[item.kind] || item.kind }}</span>
            <time>{{ stampOf(item.ts) }}</time>
          </div>
          <template v-if="item.kind === 'reply_final'">
            <pre v-for="(chunk, index) in item.detail.chunks || []" :key="index" class="trace-text">{{ chunk }}</pre>
          </template>
          <template v-else-if="item.kind === 'reply_failed'">
            <p>错误：{{ errorText(item.detail.error) }}{{ item.detail.retryable ? "（会重试）" : "" }}</p>
            <pre class="trace-text">{{ item.detail.fallback }}</pre>
          </template>
          <pre v-else-if="Object.keys(item.detail).length" class="trace-text">{{ prettyText(item.detail) }}</pre>
        </div>
      </section>

      <section v-for="round in rounds" :key="round.index" class="trace-block">
        <h3>
          第 {{ round.index + 1 }} 轮请求
          <small>
            {{ round.call.model }} · {{ round.call.finish_reason || "无结束原因" }} · {{ durationLabel(round.call.elapsed_ms) }}
            <template v-if="round.call.usage?.prompt_tokens != null"> · {{ round.call.usage.prompt_tokens }} → {{ round.call.usage.completion_tokens ?? 0 }} tokens</template>
          </small>
        </h3>
        <p v-if="round.skipped && !showAll[round.index]" class="trace-sub">
          前 {{ round.skipped }} 条消息与上一轮相同。
          <button type="button" class="text-link" @click="showAll[round.index] = true">显示全部</button>
        </p>
        <TraceMessage v-for="(message, index) in showAll[round.index] ? round.call.request.messages : round.messages" :key="index" :message="message" />
        <details class="trace-fold">
          <summary>请求参数（工具、选项）</summary>
          <pre>{{ prettyText({ tools: round.call.request.tools, tool_choice: round.call.request.tool_choice, native_search: round.call.request.native_search, options: round.call.request.options, json_output: round.call.request.json_output }) }}</pre>
        </details>
        <div v-if="round.call.response" class="trace-response">
          <p class="trace-sub">模型回应</p>
          <TraceMessage :message="round.call.response" />
        </div>
        <div v-if="round.call.error" class="trace-block bad">
          <p>
            这一轮出错：{{ errorText(round.call.error) }}<template v-if="round.call.status"> · HTTP {{ round.call.status }}</template>
          </p>
          <pre v-if="round.call.error_detail" class="trace-text">{{ round.call.error_detail }}</pre>
        </div>
        <details v-if="round.call.search" class="trace-fold">
          <summary>原生搜索来源</summary>
          <pre>{{ prettyText(round.call.search) }}</pre>
        </details>
        <details v-if="round.call.attempts?.length > 1" class="trace-fold">
          <summary>该轮内部重试（{{ round.call.attempts.length }} 次）</summary>
          <pre>{{ prettyText(round.call.attempts) }}</pre>
        </details>
      </section>
      <p v-if="!rounds.length" class="trace-sub">这个任务没有发出模型请求（失败发生在请求之前，原因见上方）。</p>
    </div>
  </el-drawer>
</template>
