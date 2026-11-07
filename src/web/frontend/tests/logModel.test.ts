import test from "node:test";
import assert from "node:assert/strict";
import { entryCopy, entryTime, levelTag, logQuery, sourceLabel, type LogEntry } from "../src/logModel.ts";

test("log query only sends fixed parameters and trims search text", () => {
  assert.equal(logQuery("bot", "warning", "  timeout "), "/logs?source=bot&level=warning&limit=200&q=timeout");
  assert.equal(logQuery("all", "all", "   "), "/logs?source=all&level=all&limit=200");
  const long = new URLSearchParams(logQuery("all", "all", "x".repeat(500)).split("?")[1]);
  assert.equal(long.get("q")?.length, 200);
});

test("entries without their own timestamp are marked approximate", () => {
  assert.equal(entryTime({ at: 1, stamp: "10-30 09:18:30", approx: false }), "10-30 09:18:30");
  assert.equal(entryTime({ at: null, stamp: "", approx: false }), "时间未知");
  assert.match(entryTime({ at: 1_700_000_000, stamp: "", approx: true }), /^≈ \d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
});

test("copied entries keep the traceback and repeat count", () => {
  const entry: LogEntry = { source: "bot", level: "ERROR", at: null, stamp: "10-30 09:18:30", approx: false,
    text: "生成未启动 task=12", detail: "Traceback (most recent call last):\nKeyError: 'reply'", count: 3, hidden: false };
  assert.equal(entryCopy(entry), "10-30 09:18:30 ERROR [机器人] 生成未启动 task=12 (×3)\nTraceback (most recent call last):\nKeyError: 'reply'");
  assert.equal(levelTag("CRITICAL"), "danger");
  assert.equal(levelTag("WARNING"), "warning");
  assert.equal(sourceLabel("unknown"), "unknown");
});
