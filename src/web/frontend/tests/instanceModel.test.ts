import test from "node:test";
import assert from "node:assert/strict";
import { blockedText, errorText, instanceStatus } from "../src/instanceModel.ts";

const row = (fields: Record<string, any>) => ({ id: "friends", enabled: true, runtime_state: "running", config_pending: false, error: "", ...fields });

test("errors win over every other state and unknown codes stay readable", () => {
  assert.equal(instanceStatus(row({ error: "instance_target_not_allowed" }), ["friends"]).label, "出错");
  assert.match(errorText("something_new"), /something_new.*运行日志/);
});

test("enabled chats without send authorization are flagged before runtime state", () => {
  assert.equal(instanceStatus(row({}), ["friends"]).label, "待授权");
  assert.equal(instanceStatus(row({ enabled: false, runtime_state: "stopped" }), ["friends"]).label, "已停用");
});

test("running instances report unapplied edits", () => {
  assert.deepEqual(instanceStatus(row({})).tone, "ok");
  assert.equal(instanceStatus(row({ config_pending: true })).label, "有未应用的修改");
});

test("stopped bot, disabled and pending requests", () => {
  assert.equal(instanceStatus(row({ runtime_state: "waiting_service" })).label, "等待机器人启动");
  assert.equal(instanceStatus(row({ enabled: false, runtime_state: "stopped" })).label, "已停用");
  assert.equal(instanceStatus(row({ runtime_state: "stopped" })).label, "未运行");
  assert.equal(instanceStatus(row({}), [], true).label, "正在处理");
  assert.equal(instanceStatus(row({ runtime_state: "unknown" })).label, "状态未知");
});

test("blocked delivery reasons become sentences", () => {
  assert.match(blockedText("send_confirmation_timeout:no_outbound_record: 无匹配发出记录"), /暂停发送/);
  assert.match(blockedText("native_text_limit:text_too_long"), /字符上限/);
  assert.equal(blockedText(""), "");
  assert.equal(blockedText("其他原因"), "其他原因");
});
