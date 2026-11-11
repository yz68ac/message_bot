<script setup lang="ts">
import { computed } from "vue";
import { clockOf, contentText, isFresh, parsePayload, prettyText, senderLabel } from "../traceModel";

const props = defineProps<{ message: any }>();

const roleNames: Record<string, string> = { system: "系统提示词", user: "用户消息", assistant: "模型", tool: "工具结果" };
const role = computed<string>(() => props.message?.role ?? "?");
const text = computed(() => contentText(props.message?.content));
// A reply request carries the chat as one JSON document in the user message; show it as a chat, not as a blob.
const payload = computed(() => (role.value === "user" ? parsePayload(props.message?.content) : null));
const calls = computed<any[]>(() => (Array.isArray(props.message?.tool_calls) ? props.message.tool_calls : []));
const preview = computed(() => {
  const line = text.value.split("\n").find((item) => item.trim()) ?? "";
  return `${line.length > 70 ? line.slice(0, 70) + "…" : line}（${text.value.length} 字符）`;
});
const fresh = computed(() => payload.value?.messages.filter((m) => isFresh(payload.value!, m)) ?? []);
const history = computed(() => payload.value?.messages.filter((m) => !isFresh(payload.value!, m)) ?? []);
const extra = (message: Record<string, unknown>) => {
  const { id, time, sender, text: body, ...rest } = message;
  return Object.keys(rest).length ? JSON.stringify(rest, null, 0) : "";
};
const restJson = computed(() => (payload.value && Object.keys(payload.value.rest).length ? JSON.stringify(payload.value.rest, null, 2) : ""));
const members = computed(() => (payload.value ? JSON.stringify(payload.value.members, null, 2) : ""));
</script>

<template>
  <div :class="['trace-msg', 'role-' + role]">
    <div class="trace-msg-head">
      <span class="trace-role">{{ roleNames[role] || role }}</span>
      <code v-if="message.tool_call_id" class="trace-id">{{ message.tool_call_id }}</code>
    </div>

    <template v-if="payload">
      <p class="trace-sub">本轮要回应的新消息（{{ fresh.length }} 条）</p>
      <ol class="chat-rows fresh">
        <li v-for="item in fresh" :key="item.id">
          <span class="chat-id">#{{ item.id }}</span>
          <span class="chat-time">{{ item.time ? clockOf(Number(item.time)) : "" }}</span>
          <b class="chat-sender">{{ senderLabel(payload, item.sender) }}</b>
          <span class="chat-text">{{ item.text }}</span>
          <code v-if="extra(item)" class="chat-extra">{{ extra(item) }}</code>
        </li>
      </ol>
      <details v-if="history.length" class="trace-fold">
        <summary>聊天历史（{{ history.length }} 条，模型一并看到）</summary>
        <ol class="chat-rows">
          <li v-for="item in history" :key="item.id">
            <span class="chat-id">#{{ item.id }}</span>
            <span class="chat-time">{{ item.time ? clockOf(Number(item.time)) : "" }}</span>
            <b class="chat-sender">{{ senderLabel(payload, item.sender) }}</b>
            <span class="chat-text">{{ item.text }}</span>
            <code v-if="extra(item)" class="chat-extra">{{ extra(item) }}</code>
          </li>
        </ol>
      </details>
      <p v-else class="trace-sub">没有更早的聊天历史。</p>
      <details v-if="payload.memory.length" class="trace-fold">
        <summary>记忆背景（{{ payload.memory.length }} 行）</summary>
        <pre>{{ payload.memory.join("\n") }}</pre>
      </details>
      <details v-if="restJson || members" class="trace-fold">
        <summary>其他字段（成员、统计等）</summary>
        <pre>{{ restJson }}{{ restJson && members ? "\n" : "" }}{{ members }}</pre>
      </details>
    </template>

    <details v-else-if="role === 'system' || role === 'tool'" class="trace-fold">
      <summary>{{ preview }}</summary>
      <pre>{{ role === "tool" ? prettyText(text) : text }}</pre>
    </details>
    <pre v-else-if="text" class="trace-text">{{ text }}</pre>

    <div v-for="call in calls" :key="call.id" class="trace-call">
      <b>调用工具 {{ call.function?.name }}</b>
      <pre>{{ prettyText(call.function?.arguments ?? "") }}</pre>
    </div>
  </div>
</template>
