#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const corePath = path.join(__dirname, "..", "ProviderCore.js");
const source = fs.readFileSync(corePath, "utf8").replace(/^\.pragma library\s*/, "");
const sandbox = {};
vm.runInNewContext(source, sandbox, { filename: corePath });
const Core = sandbox;

let passed = 0;
function test(name, fn) {
    fn();
    passed++;
    process.stdout.write(`ok ${passed} - ${name}\n`);
}

function fixture({ id = 77, app = "Proof Producer", summary = "Controlled", body = "Body 1", label = "Open action", actions } = {}) {
    const defaultAction = {
        identifier: "default",
        text: label,
        calls: 0,
        invoke() { this.calls++; }
    };
    const notification = {
        id,
        tracked: true,
        appName: app,
        summary,
        body,
        actions: actions || [
            { identifier: "reply", text: "Reply", invoke() {} },
            defaultAction
        ]
    };
    return {
        wrapper: { notification },
        notification,
        action: defaultAction
    };
}

function bindPayload({ session = 100, boot_id = 200, id = 1, rev = 1, source_version = 1, desktop_id = 77, expected } = {}) {
    return JSON.stringify({
        v: 1,
        session,
        boot_id,
        id,
        rev,
        source_version,
        desktop_id,
        expected: expected || { app: "Proof Producer", summary: "Controlled", body: "Body 1", default_label: "Open action" }
    });
}

function bind(state, payload, wrappers) {
    return Core.bind(state, Core.parseBindPayload(payload), wrappers);
}

function activatePayload(state, record, request, overrides = {}) {
    return JSON.stringify(Object.assign({
        v: 1,
        epoch: state.epoch,
        session: state.session,
        boot_id: state.boot_id,
        id: record.id,
        rev: record.rev,
        token: record.token,
        request
    }, overrides));
}

function releasePayload(state, record, final) {
    return JSON.stringify({
        v: 1,
        session: state.session,
        boot_id: state.boot_id,
        id: record.id,
        rev: record.rev,
        token: record.token,
        final
    });
}

test("request parsing rejects booleans, malformed JSON, extra fields, and UTF-8 overflow", () => {
    assert.equal(Core.parseBindPayload("{").ok, false);
    assert.equal(Core.parseBindPayload(JSON.stringify({ ...JSON.parse(bindPayload()), id: true })).ok, false);
    assert.equal(Core.parseBindPayload(JSON.stringify({ ...JSON.parse(bindPayload()), desktop_id: 0 })).ok, false);
    assert.equal(Core.parseBindPayload(JSON.stringify({ ...JSON.parse(bindPayload()), extra: "x" })).ok, false);
    assert.equal(Core.parseBindPayload(JSON.stringify({ ...JSON.parse(bindPayload()), expected: { app: "a", summary: "b", body: "🙂".repeat(2100), default_label: "x" } })).ok, false);
});

test("bind selects the unique explicit default action and duplicates do not churn its token", () => {
    const f = fixture();
    const state = Core.createState("epoch-a", 1234);
    const payload = bindPayload();
    const first = bind(state, payload, [f.wrapper]);
    assert.equal(first.response.status, "ready");
    assert.equal(first.record.action, f.action);
    assert.notEqual(first.record.action, f.notification.actions[0]);
    const duplicate = bind(state, payload, [f.wrapper]);
    assert.equal(duplicate.response.token, first.response.token);
    assert.equal(duplicate.duplicate, true);
    assert.equal(Core.status(state, [f.wrapper]).bindings[0].live, true);
});

test("no default, duplicate default keys, and oversized action lists are unavailable", () => {
    const state = Core.createState("epoch-a", 1234);
    const noDefault = fixture({ actions: [{ identifier: "reply", text: "Reply" }] });
    assert.equal(bind(state, bindPayload(), [noDefault.wrapper]).response.status, "unavailable");

    const duplicateDefault = fixture({ actions: [
        { identifier: "default", text: "First" },
        { identifier: "default", text: "Second" }
    ] });
    assert.equal(bind(state, bindPayload(), [duplicateDefault.wrapper]).response.status, "unavailable");

    const tooMany = fixture({ actions: Array.from({ length: 65 }, (_, i) => ({ identifier: `action-${i}`, text: "x" })) });
    assert.equal(bind(state, bindPayload(), [tooMany.wrapper]).response.status, "unavailable");
});

test("desktop ID ambiguity fails closed before matching raw fields", () => {
    const expected = { app: "Proof Producer", summary: "Controlled", body: "Body 1", default_label: "Open action" };
    const matching = fixture();
    const replacement = fixture({ summary: "Different summary" });
    const state = Core.createState("epoch-a", 1234);
    const ambiguous = bind(state, bindPayload({ expected }), [matching.wrapper, replacement.wrapper]);
    assert.equal(ambiguous.response.status, "unavailable");
    assert.equal(ambiguous.response.error, "ambiguous_notification");

    const mismatch = bind(state, bindPayload({ expected }), [replacement.wrapper]);
    assert.equal(mismatch.response.status, "unavailable");
    assert.equal(mismatch.response.error, "notification_identity_mismatch");
});

test("dispatch is one synchronous invocation and cached duplicates never invoke twice", () => {
    const f = fixture();
    const state = Core.createState("epoch-a", 1234);
    const result = bind(state, bindPayload(), [f.wrapper]);
    const record = result.record;
    const payload = activatePayload(state, record, 1);
    const invoke = action => action.invoke();
    const dispatched = Core.activate(state, Core.parseActivatePayload(payload), [f.wrapper], invoke);
    assert.equal(dispatched.status, "dispatched");
    assert.equal(f.action.calls, 1);
    const duplicate = Core.activate(state, Core.parseActivatePayload(payload), [f.wrapper], invoke);
    assert.equal(duplicate.status, "dispatched");
    assert.equal(f.action.calls, 1);
    const old = Core.activate(state, Core.parseActivatePayload(activatePayload(state, record, 1, { token: "wrong" })), [f.wrapper], invoke);
    assert.equal(old.status, "stale");
    assert.equal(f.action.calls, 1);
});

test("close, field changes, and action changes revoke exact bindings", () => {
    const f = fixture();
    const state = Core.createState("epoch-a", 1234);
    const result = bind(state, bindPayload(), [f.wrapper]);
    const payload = activatePayload(state, result.record, 1);
    f.notification.body = "changed";
    assert.equal(Core.status(state, [f.wrapper]).bindings[0].live, false);
    assert.equal(Core.activate(state, Core.parseActivatePayload(payload), [f.wrapper], () => assert.fail("stale action invoked")).status, "stale");
    assert.equal(bind(state, bindPayload(), [f.wrapper]).response.status, "stale");

    const newer = bind(state, bindPayload({ rev: 2, source_version: 2, expected: { app: "Proof Producer", summary: "Controlled", body: "changed", default_label: "Open action" } }), [f.wrapper]);
    assert.equal(newer.response.status, "ready");
    const replacementAction = { identifier: "default", text: "Open action", invoke() {} };
    f.notification.actions = [{ identifier: "reply", text: "Reply" }, replacementAction];
    assert.equal(Core.status(state, [f.wrapper]).bindings[0].live, false);
    assert.equal(bind(state, bindPayload({ rev: 2, source_version: 2, expected: { app: "Proof Producer", summary: "Controlled", body: "changed", default_label: "Open action" } }), [f.wrapper]).response.status, "stale");
});

test("temporary release revokes a token but can rebind the same live object; final release fences it", () => {
    const f = fixture();
    const state = Core.createState("epoch-a", 1234);
    const first = bind(state, bindPayload(), [f.wrapper]);
    const oldRecord = first.record;
    const oldToken = oldRecord.token;
    const revoked = Core.release(state, Core.parseReleasePayload(releasePayload(state, oldRecord, false)), [f.wrapper]);
    assert.equal(revoked.response.status, "revoked");
    const afterRevoke = Core.status(state, [f.wrapper]).bindings[0];
    assert.equal(afterRevoke.live, false);
    assert.equal(oldRecord.active, true);
    const rebound = bind(state, bindPayload({ rev: 2, source_version: 1 }), [f.wrapper]);
    assert.equal(rebound.response.status, "ready");
    assert.notEqual(rebound.response.token, oldToken);
    const final = Core.release(state, Core.parseReleasePayload(releasePayload(state, rebound.record, true)), [f.wrapper]);
    assert.equal(final.response.status, "released");
    assert.equal(bind(state, bindPayload({ rev: 3, source_version: 2 }), [f.wrapper]).response.status, "stale");
    assert.equal(bind(state, bindPayload({ id: 1, source_version: 3 }), [f.wrapper]).response.status, "stale");
});

test("delayed releases cannot revoke a replacement token; uncertain invoke is never retried", () => {
    const f = fixture();
    const state = Core.createState("epoch-a", 1234);
    const first = bind(state, bindPayload(), [f.wrapper]);
    const oldRecord = first.record;
    const oldRev = oldRecord.rev;
    const oldToken = oldRecord.token;
    Core.release(state, Core.parseReleasePayload(releasePayload(state, oldRecord, false)), [f.wrapper]);
    const rebound = bind(state, bindPayload({ rev: 2, source_version: 1 }), [f.wrapper]);
    const delayedRelease = Core.release(state, Core.parseReleasePayload(JSON.stringify({
        v: 1,
        session: state.session,
        boot_id: state.boot_id,
        id: 1,
        rev: oldRev,
        token: oldToken,
        final: true
    })), [f.wrapper]);
    assert.equal(delayedRelease.response.status, "stale");
    assert.equal(Core.status(state, [f.wrapper]).bindings[0].live, true);

    const payload = activatePayload(state, rebound.record, 1);
    const uncertain = Core.activate(state, Core.parseActivatePayload(payload), [f.wrapper], () => {
        f.action.calls++;
        throw new Error("dispatch result uncertain");
    });
    assert.equal(uncertain.status, "unknown");
    const duplicate = Core.activate(state, Core.parseActivatePayload(payload), [f.wrapper], () => assert.fail("unknown dispatch retried"));
    assert.equal(duplicate.status, "unknown");
    assert.equal(f.action.calls, 1);
});

test("a boot change revokes old tokens and only rebinds the same live refs", () => {
    const f = fixture();
    const state = Core.createState("epoch-a", 1234);
    const first = bind(state, bindPayload(), [f.wrapper]);
    const oldToken = first.response.token;
    const rebound = bind(state, bindPayload({ boot_id: 201, rev: 1, source_version: 1 }), [f.wrapper]);
    assert.equal(rebound.response.status, "ready");
    assert.notEqual(rebound.response.token, oldToken);
    const oldBoot = Core.activate(state, Core.parseActivatePayload(JSON.stringify({
        v: 1, epoch: state.epoch, session: state.session, boot_id: 200,
        id: 1, rev: 1, token: oldToken, request: 1
    })), [f.wrapper], () => assert.fail("old boot action invoked"));
    assert.equal(oldBoot.status, "stale");
    f.notification.tracked = false;
    const changed = bind(state, bindPayload({ boot_id: 202, rev: 2, source_version: 2 }), [f.wrapper]);
    assert.equal(changed.response.status, "unavailable");
});

test("per-card source versions permit out-of-order unrelated binds without permitting same-version object swaps", () => {
    const a = fixture({ id: 77, summary: "A" });
    const b = fixture({ id: 78, summary: "B" });
    const state = Core.createState("epoch-a", 1234);
    const bindA = bind(state, bindPayload({ id: 1, desktop_id: 77, source_version: 10, expected: { app: "Proof Producer", summary: "A", body: "Body 1", default_label: "Open action" } }), [a.wrapper, b.wrapper]);
    const bindB = bind(state, bindPayload({ id: 2, desktop_id: 78, source_version: 2, expected: { app: "Proof Producer", summary: "B", body: "Body 1", default_label: "Open action" } }), [a.wrapper, b.wrapper]);
    assert.equal(bindA.response.status, "ready");
    assert.equal(bindB.response.status, "ready");
    const swapped = fixture({ id: 77, summary: "A" });
    assert.equal(bind(state, bindPayload({ id: 1, desktop_id: 77, source_version: 10, expected: { app: "Proof Producer", summary: "A", body: "Body 1", default_label: "Open action" } }), [swapped.wrapper]).response.status, "stale");
});

process.stdout.write(`1..${passed}\n`);
