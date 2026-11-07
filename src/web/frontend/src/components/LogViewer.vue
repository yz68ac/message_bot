<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { Refresh } from "@element-plus/icons-vue";
import { api } from "../api";
import {
  entryCopy,
  entryTime,
  levelLabel,
  levelTag,
  logQuery,
  logSources,
  readErrorLabel,
  sourceLabel,
  type LogEntry,
  type LogFilter,
} from "../logModel";

const props = withDefaults(defineProps<{ source?: string; level?: LogFilter }>(), {
  source: "all",
  level: "all",
});
const emit = defineEmits<{ error: [error: any] }>();

const source = ref(props.source),
  level = ref<LogFilter>(props.level),
  search = ref(""),
  follow = ref(true);
const entries = ref<LogEntry[]>([]),
  infos = ref<any[]>([]),
  total = ref(0),
  busy = ref(false),
  error = ref("");
let sequence = 0,
  timer: ReturnType<typeof setInterval> | undefined,
  debounce: ReturnType<typeof setTimeout> | undefined;

async function load(quiet = false) {
  const current = ++sequence;
  if (!quiet) busy.value = true;
  try {
    const data = await api(logQuery(source.value, level.value, search.value));
    if (current !== sequence) return; // A newer filter already replaced this request.
    entries.value = data.entries;
    infos.value = data.sources;
    total.value = data.total;
    error.value = "";
  } catch (e: any) {
    if (current !== sequence) return;
    error.value = e.message;
    if (e.status === 401) emit("error", e);
  } finally {
    if (current === sequence) busy.value = false;
  }
}

const unreadable = () =>
  infos.value.filter((info) => info.error && (source.value !== "all" || info.error !== "missing"));
const truncated = () => infos.value.some((info) => info.truncated);
const lines = (entry: LogEntry) => entry.detail.split("\n").length;

async function copy(entry: LogEntry) {
  try {
    await navigator.clipboard.writeText(entryCopy(entry));
    ElMessage.success("已复制这条日志。");
  } catch {
    ElMessage.error("浏览器不允许复制，请手动选中文本。");
  }
}

watch([source, level], () => void load());
watch(search, () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => void load(), 300);
});
onMounted(() => {
  void load();
  timer = setInterval(() => {
    if (follow.value && !busy.value && document.visibilityState === "visible") void load(true);
  }, 5000);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  clearTimeout(debounce);
});
defineExpose({ load });
</script>

<template>
  <section class="panel logs-panel">
    <div class="logs-toolbar">
      <el-radio-group v-model="source" size="small" class="logs-sources" aria-label="日志来源">
        <el-radio-button v-for="item in logSources" :key="item.id" :value="item.id">{{
          item.label
        }}</el-radio-button>
      </el-radio-group>
      <div class="logs-controls">
        <el-select v-model="level" size="small" class="logs-level" aria-label="日志级别">
          <el-option value="all" label="全部级别" />
          <el-option value="warning" label="警告及以上" />
          <el-option value="error" label="仅错误" />
        </el-select>
        <el-input v-model="search" size="small" clearable placeholder="搜索日志内容" class="logs-search" />
        <el-switch v-model="follow" size="small" active-text="自动刷新" />
        <el-button size="small" :icon="Refresh" :loading="busy" @click="load()">刷新</el-button>
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="logs-alert" />
    <el-alert
      v-for="info in unreadable()"
      :key="info.id"
      :title="`${sourceLabel(info.id)}：${readErrorLabel[info.error] || '日志暂时无法读取。'}`"
      :type="info.error === 'missing' ? 'info' : 'warning'"
      :closable="false"
      class="logs-alert"
    />
    <div v-if="!entries.length && !busy" class="empty-state">
      {{ level === "all" ? "没有可显示的日志。" : "没有符合条件的警告或错误。" }}
    </div>
    <ol v-else class="log-list">
      <li v-for="(entry, index) in entries" :key="index" :class="['log-entry', 'level-' + entry.level.toLowerCase()]">
        <div class="log-head">
          <time>{{ entryTime(entry) }}</time>
          <el-tag size="small" :type="levelTag(entry.level)" effect="light" disable-transitions>{{
            levelLabel[entry.level] || entry.level
          }}</el-tag>
          <span v-if="source === 'all'" class="log-source">{{ sourceLabel(entry.source) }}</span>
          <span v-if="entry.count > 1" class="log-count" title="连续重复的同一条日志">×{{ entry.count }}</span>
          <button class="log-copy" type="button" @click="copy(entry)">复制</button>
        </div>
        <code class="log-text">{{ entry.text }}</code>
        <details v-if="entry.detail" class="log-detail">
          <summary>堆栈与详情（{{ lines(entry) }} 行）</summary>
          <pre>{{ entry.detail }}</pre>
        </details>
      </li>
    </ol>
    <div class="logs-footnote">
      <span>显示 {{ entries.length }} / {{ total }} 条{{ truncated() ? " · 只读取每个文件最近 512 KB" : "" }}</span>
      <span>普通信息只保留事件名和状态码；警告、错误与堆栈完整显示，密钥和长令牌已自动隐藏。</span>
    </div>
  </section>
</template>
