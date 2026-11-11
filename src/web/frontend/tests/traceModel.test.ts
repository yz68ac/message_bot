import test from "node:test";
import assert from "node:assert/strict";
import {
  emptyFilters,
  filtersOfRoute,
  listKey,
  locateQuery,
  newMessages,
  outcome,
  pageOfHash,
  parsePayload,
  prettyText,
  senderLabel,
  traceHash,
  traceQuery,
  traceRouteOfHash,
} from "../src/traceModel.ts";

test("the page is the hash path even when a query follows it", () => {
  assert.equal(pageOfHash("#/trace?id=2&task=11"), "trace");
  assert.equal(pageOfHash("#/logs"), "logs");
  assert.equal(pageOfHash("#overview"), "overview");
  assert.equal(pageOfHash(""), "");
});

test("a trace route survives a round trip and drops anything that is not well formed", () => {
  const route = { id: 2, instance: "private_demo", role: "reply", task: 11, run: "20261110-0900-ab12", problems: true };
  assert.equal(traceHash(route), "#/trace?id=2&instance=private_demo&role=reply&task=11&run=20261110-0900-ab12&problems=true");
  assert.deepEqual(traceRouteOfHash(traceHash(route)), route);
  assert.equal(traceHash({}), "#/trace");
  assert.deepEqual(traceRouteOfHash("#/trace"), {});
  assert.deepEqual(traceRouteOfHash("#/trace?id=abc&task=-1&instance=../x&role=admin&run=a%20b&problems=1"), {});
});

test("the list query sends only set filters and trims the search text", () => {
  assert.equal(traceQuery(emptyFilters()), "/trace?limit=100");
  const filters = { ...emptyFilters(), instance: "alpha", role: "reply", task: 7, problems: true, q: "  咖啡 " };
  const params = new URLSearchParams(traceQuery(filters, 42, 50).split("?")[1]);
  assert.deepEqual(Object.fromEntries(params), { limit: "50", instance: "alpha", role: "reply", task: "7", problems: "true", q: "咖啡", before: "42" });
  assert.equal(new URLSearchParams(traceQuery({ ...emptyFilters(), q: "x".repeat(500) }).split("?")[1]).get("q")?.length, 200);
});

test("filters follow the route and only list-changing fields change the list key", () => {
  assert.deepEqual(filtersOfRoute({ instance: "a", task: 3, problems: true }), { ...emptyFilters(), instance: "a", task: 3, problems: true });
  assert.equal(listKey({ id: 1, instance: "a" }), listKey({ id: 9, instance: "a" }));
  assert.notEqual(listKey({ instance: "a" }), listKey({ instance: "b" }));
});

test("a log link asks for the job that started before the line", () => {
  assert.equal(locateQuery({ instance: "alpha", role: "reply", task: 11 }, 1700000000.5), "/trace/locate?instance=alpha&role=reply&task=11&at=1700000000.5");
  assert.equal(locateQuery({ instance: "alpha", role: "memory", task: 2 }, null), "/trace/locate?instance=alpha&role=memory&task=2");
});

test("outcomes name the error code and flag calls the provider cut short", () => {
  assert.deepEqual(outcome({ ok: 0, error: "empty_reply", finish: "stop" }), { label: "模型返回了空回复（empty_reply）", tag: "danger" });
  assert.deepEqual(outcome({ ok: 0, error: "http_429", finish: "" }), { label: "http_429", tag: "danger" });
  assert.deepEqual(outcome({ ok: 0, error: "", finish: "" }), { label: "失败", tag: "danger" });
  assert.deepEqual(outcome({ ok: 1, error: "", finish: "length" }), { label: "结束原因 length", tag: "warning" });
  assert.deepEqual(outcome({ ok: 1, error: "", finish: "stop" }), { label: "成功", tag: "success" });
});

test("the chat payload is found in plain or multi-part user content", () => {
  const body = { bot: "wxid_bot", members: { wxid_a: { alias: "小林" } }, messages: [{ id: 1, sender: "wxid_a", text: "早" }, { id: 2, sender: "wxid_a", text: "几点" }],
    source_ids: [2], memory: ["小林常问行情"], history_omitted: 3 };
  for (const content of [JSON.stringify(body), [{ type: "text", text: JSON.stringify(body) }, { type: "image_url", image_url: { url: "[图片 image/png, 1 KB]" } }]]) {
    const payload = parsePayload(content);
    assert.deepEqual(payload?.source_ids, [2]);
    assert.equal(payload?.messages.length, 2);
    assert.deepEqual(payload?.memory, ["小林常问行情"]);
    assert.deepEqual(payload?.rest, { bot: "wxid_bot", history_omitted: 3 });
    assert.equal(senderLabel(payload!, "wxid_a"), "小林");
    assert.equal(senderLabel(payload!, "wxid_unknown_member_with_a_long_id"), "wxid_unknown_membe…");
  }
  assert.equal(parsePayload("不是 JSON"), null);
  assert.equal(parsePayload(JSON.stringify({ messages: [] })), null);
  assert.equal(parsePayload(null), null);
});

test("later rounds show only the messages that are new", () => {
  const system = { role: "system", content: "规则" },
    user = { role: "user", content: "问" },
    call = { role: "assistant", tool_calls: [{ id: "c1" }] },
    tool = { role: "tool", content: "{}" };
  const calls = [{ request: { messages: [system, user] } }, { request: { messages: [system, user, call, tool] } }, { request: { messages: [system, { ...user, content: "改过" }] } }];
  assert.deepEqual(newMessages(calls, 0), { skipped: 0, messages: [system, user] });
  assert.deepEqual(newMessages(calls, 1), { skipped: 2, messages: [call, tool] });
  assert.deepEqual(newMessages(calls, 2), { skipped: 1, messages: [{ ...user, content: "改过" }] });
  assert.deepEqual(newMessages([{}], 0), { skipped: 0, messages: [] });
});

test("tool output is shown as indented JSON when it parses", () => {
  assert.equal(prettyText('{"a":1}'), '{\n  "a": 1\n}');
  assert.equal(prettyText("纯文本"), "纯文本");
  assert.equal(prettyText({ b: [1] }), '{\n  "b": [\n    1\n  ]\n}');
});
