import test from "node:test";
import assert from "node:assert/strict";
import { authorizeAction, setupState } from "../src/setupModel.ts";

const keyed = { providers: [{ id: "deepseek", configured: true }, { id: "openai", configured: false }] };

function snapshot({ phase = "logged_in", bound = true, bot = "inactive", instances = 1, pending = [] as string[] } = {}) {
  return {
    runtime: { login_phase: phase },
    services: { bot: { state: bot } },
    account_setup: { bound, pending_instances: pending },
    instances: Array.from({ length: instances }, (_, i) => ({ id: `chat${i}`, enabled: true })),
  };
}

test("fresh install starts at the model key step", () => {
  const state = setupState(snapshot({ phase: "qr_required", bound: false, instances: 0 }), { providers: [] });
  assert.equal(state.next, "keys");
  assert.equal(state.launch, null);
  assert.match(state.steps[0].detail, /provider\.env/);
});

test("logged in but unbound asks to connect the account before instances", () => {
  const state = setupState(snapshot({ bound: false, instances: 0 }), keyed);
  assert.equal(state.next, "login");
  assert.equal(state.steps[1].title, "连接当前微信账号");
});

test("pending chats offer authorize; a stopped bot is started afterwards", () => {
  const stopped = setupState(snapshot({ pending: ["chat0"] }), keyed);
  assert.equal(stopped.next, "launch");
  assert.equal(stopped.launch, "authorize_start");
  const running = setupState(snapshot({ pending: ["chat0"], bot: "active" }), keyed);
  assert.equal(running.launch, "authorize");
  assert.match(running.steps[3].detail, /短暂重启/);
});

test("authorized but stopped only needs a start", () => {
  const state = setupState(snapshot(), keyed);
  assert.equal(state.launch, "start");
  assert.equal(state.next, "launch");
});

test("everything done has no next step", () => {
  const state = setupState(snapshot({ bot: "active" }), keyed);
  assert.equal(state.next, null);
  assert.equal(state.launch, null);
  assert.deepEqual(state.steps.map((step) => step.done), [true, true, true, true]);
});

test("a live login phase from the QR dialog overrides the snapshot", () => {
  const state = setupState(snapshot({ phase: "qr_required", bound: true }), keyed, "logged_in");
  assert.equal(state.loggedIn, true);
  assert.equal(authorizeAction({ services: { bot: { state: "activating" } } }), "authorize");
});
