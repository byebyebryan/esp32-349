.pragma library

var VERSION = 1;
var MAX_MESSAGE_BYTES = 8192;
var MAX_ACTIONS = 64;
var MAX_BINDINGS = 32;
var MAX_RETIRED_SCOPES = 64;
var MAX_RECENT_RESULTS = 64;
var IDENTITY_MAX = 0x7fffffff;
var UINT32_MAX = 0xffffffff;

function utf8Length(text) {
    var bytes = 0;
    for (var i = 0; i < text.length; i++) {
        var code = text.charCodeAt(i);
        if (code < 0x80) {
            bytes += 1;
        } else if (code < 0x800) {
            bytes += 2;
        } else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
            var low = text.charCodeAt(i + 1);
            if (low >= 0xdc00 && low <= 0xdfff) {
                bytes += 4;
                i++;
            } else {
                bytes += 3;
            }
        } else {
            bytes += 3;
        }
        if (bytes > MAX_MESSAGE_BYTES)
            return bytes;
    }
    return bytes;
}

function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKeys(value, allowed) {
    var keys = Object.keys(value);
    for (var i = 0; i < keys.length; i++) {
        if (allowed.indexOf(keys[i]) === -1)
            return false;
    }
    return true;
}

function positiveInt(value, maximum) {
    return typeof value === "number" && isFinite(value) && Math.floor(value) === value && value > 0 && value <= maximum;
}

function boundedString(value, maximum) {
    return typeof value === "string" && value.length > 0 && value.length <= maximum;
}

function parseJsonPayload(raw) {
    if (typeof raw !== "string" || utf8Length(raw) > MAX_MESSAGE_BYTES)
        return { ok: false, error: "invalid_size_or_type" };
    var value;
    try {
        value = JSON.parse(raw);
    } catch (e) {
        return { ok: false, error: "invalid_json" };
    }
    if (!isObject(value))
        return { ok: false, error: "invalid_shape" };
    return { ok: true, value: value };
}

function parseBindPayload(raw) {
    var parsed = parseJsonPayload(raw);
    if (!parsed.ok)
        return parsed;
    var value = parsed.value;
    if (!hasOnlyKeys(value, ["v", "session", "boot_id", "id", "rev", "source_version", "desktop_id", "expected"]))
        return { ok: false, error: "unknown_field" };
    if (value.v !== VERSION
            || !positiveInt(value.session, IDENTITY_MAX)
            || !positiveInt(value.boot_id, UINT32_MAX)
            || !positiveInt(value.id, IDENTITY_MAX)
            || !positiveInt(value.rev, IDENTITY_MAX)
            || !positiveInt(value.source_version, IDENTITY_MAX)
            || !positiveInt(value.desktop_id, UINT32_MAX)
            || !isObject(value.expected)
            || !hasOnlyKeys(value.expected, ["app", "summary", "body", "default_label"])
            || typeof value.expected.app !== "string"
            || typeof value.expected.summary !== "string"
            || typeof value.expected.body !== "string"
            || typeof value.expected.default_label !== "string") {
        return { ok: false, error: "invalid_bind_fields" };
    }
    return { ok: true, value: value };
}

function parseActivatePayload(raw) {
    var parsed = parseJsonPayload(raw);
    if (!parsed.ok)
        return parsed;
    var value = parsed.value;
    if (!hasOnlyKeys(value, ["v", "epoch", "session", "boot_id", "id", "rev", "token", "request"]))
        return { ok: false, error: "unknown_field" };
    if (value.v !== VERSION
            || !boundedString(value.epoch, 128)
            || !positiveInt(value.session, IDENTITY_MAX)
            || !positiveInt(value.boot_id, UINT32_MAX)
            || !positiveInt(value.id, IDENTITY_MAX)
            || !positiveInt(value.rev, IDENTITY_MAX)
            || !boundedString(value.token, 160)
            || !positiveInt(value.request, IDENTITY_MAX)) {
        return { ok: false, error: "invalid_activate_fields" };
    }
    return { ok: true, value: value };
}

function parseReleasePayload(raw) {
    var parsed = parseJsonPayload(raw);
    if (!parsed.ok)
        return parsed;
    var value = parsed.value;
    if (!hasOnlyKeys(value, ["v", "session", "boot_id", "id", "rev", "token", "final"]))
        return { ok: false, error: "unknown_field" };
    if (value.v !== VERSION
            || !positiveInt(value.session, IDENTITY_MAX)
            || !positiveInt(value.boot_id, UINT32_MAX)
            || !positiveInt(value.id, IDENTITY_MAX)
            || !positiveInt(value.rev, IDENTITY_MAX)
            || !boundedString(value.token, 160)
            || typeof value.final !== "boolean") {
        return { ok: false, error: "invalid_release_fields" };
    }
    return { ok: true, value: value };
}

function createState(epoch, pid) {
    return {
        v: VERSION,
        epoch: epoch,
        pid: pid,
        session: null,
        boot_id: null,
        records: Object.create(null),
        lineages: Object.create(null),
        releasedLocalIdHighWater: 0,
        retiredSessions: [],
        retiredBoots: [],
        nextToken: 0,
        ledgerBootId: null,
        requestHighWater: 0,
        recentResults: Object.create(null),
        recentOrder: []
    };
}

function sameExpected(a, b) {
    return !!a && !!b
        && a.app === b.app
        && a.summary === b.summary
        && a.body === b.body
        && a.default_label === b.default_label;
}

function listLength(value) {
    return value && typeof value.length === "number" && isFinite(value.length) && value.length >= 0
        ? Math.floor(value.length) : -1;
}

function containsReference(list, object) {
    var length = listLength(list);
    if (length < 0)
        return false;
    for (var i = 0; i < length; i++) {
        if (list[i] === object)
            return true;
    }
    return false;
}

function findDefaultAction(actions) {
    var length = listLength(actions);
    if (length < 0 || length > MAX_ACTIONS)
        return { ok: false, reason: "action_list_invalid" };
    var found = null;
    for (var i = 0; i < length; i++) {
        var action = actions[i];
        if (action && action.identifier === "default") {
            if (found)
                return { ok: false, reason: "ambiguous_default" };
            found = action;
        }
    }
    if (!found)
        return { ok: false, reason: "no_default" };
    return { ok: true, action: found, label: found.text };
}

function rawFieldsMatch(notification, expected) {
    return !!notification
        && notification.appName === expected.app
        && notification.summary === expected.summary
        && notification.body === expected.body;
}

function inspectCandidate(wrappers, request) {
    var length = listLength(wrappers);
    if (length < 0)
        return { ok: false, status: "unavailable", reason: "wrapper_list_unavailable" };
    var matches = [];
    for (var i = 0; i < length; i++) {
        var wrapper = wrappers[i];
        var notification = wrapper && wrapper.notification;
        if (!notification || notification.id !== request.desktop_id || notification.tracked !== true)
            continue;
        var alreadySeen = false;
        for (var j = 0; j < matches.length; j++) {
            if (matches[j].notification === notification) {
                alreadySeen = true;
                break;
            }
        }
        if (!alreadySeen)
            matches.push({ wrapper: wrapper, notification: notification });
    }
    if (matches.length === 0)
        return { ok: false, status: "unavailable", reason: "notification_not_found" };
    if (matches.length > 1)
        return { ok: false, status: "unavailable", reason: "ambiguous_notification" };
    var notification = matches[0].notification;
    if (!rawFieldsMatch(notification, request.expected))
        return { ok: false, status: "unavailable", reason: "notification_identity_mismatch" };
    var defaultInfo = findDefaultAction(notification.actions);
    if (!defaultInfo.ok)
        return { ok: false, status: "unavailable", reason: defaultInfo.reason, wrapper: matches[0].wrapper, notification: notification };
    if (defaultInfo.label !== request.expected.default_label)
        return { ok: false, status: "unavailable", reason: "default_label_mismatch", wrapper: matches[0].wrapper, notification: notification };
    return {
        ok: true,
        wrapper: matches[0].wrapper,
        notification: notification,
        action: defaultInfo.action
    };
}

function recordProblem(record, wrappers) {
    if (!record || !record.active)
        return "stale";
    var notification = record.notification;
    if (!notification || !record.wrapper || !containsReference(wrappers, record.wrapper))
        return "stale";
    if (record.wrapper.notification !== notification || notification.tracked !== true || notification.id !== record.desktop_id)
        return "stale";
    if (!rawFieldsMatch(notification, record.expected))
        return "stale";
    var defaultInfo = findDefaultAction(notification.actions);
    if (!defaultInfo.ok)
        return "unavailable";
    if (defaultInfo.action !== record.action || defaultInfo.label !== record.expected.default_label)
        return "stale";
    return "";
}

function invalidate(state, record) {
    if (!record || !record.active)
        return false;
    record.active = false;
    record.invalidated = true;
    record.tokenLive = false;
    return true;
}

function isRetired(list, value) {
    return list.indexOf(value) !== -1;
}

function appendRetired(list, value) {
    if (!isRetired(list, value))
        list.push(value);
}

function clearSessionState(state, session, bootId) {
    state.records = Object.create(null);
    state.lineages = Object.create(null);
    state.releasedLocalIdHighWater = 0;
    state.session = session;
    state.boot_id = bootId;
    state.retiredBoots = [];
    state.ledgerBootId = bootId;
    state.requestHighWater = 0;
    state.recentResults = Object.create(null);
    state.recentOrder = [];
    state.nextToken = 0;
}

function switchBoot(state, bootId, wrappers) {
    if (state.boot_id === null) {
        state.boot_id = bootId;
        state.ledgerBootId = bootId;
        state.requestHighWater = 0;
        state.recentResults = Object.create(null);
        state.recentOrder = [];
        return true;
    }
    if (isRetired(state.retiredBoots, bootId) || state.retiredBoots.length >= MAX_RETIRED_SCOPES)
        return false;
    appendRetired(state.retiredBoots, state.boot_id);
    state.boot_id = bootId;
    state.ledgerBootId = bootId;
    state.requestHighWater = 0;
    state.recentResults = Object.create(null);
    state.recentOrder = [];
    var ids = Object.keys(state.records);
    for (var i = 0; i < ids.length; i++) {
        var record = state.records[ids[i]];
        if (!record || !record.active)
            continue;
        if (recordProblem(record, wrappers)) {
            invalidate(state, record);
            continue;
        }
        record.token = null;
        record.tokenLive = false;
        record.boot_id = bootId;
        record.rebindable = true;
    }
    return true;
}

function enterScope(state, session, bootId, wrappers) {
    if (state.session === null) {
        state.session = session;
        state.boot_id = bootId;
        state.ledgerBootId = bootId;
        return { ok: true };
    }
    if (state.session !== session) {
        if (isRetired(state.retiredSessions, session) || state.retiredSessions.length >= MAX_RETIRED_SCOPES)
            return { ok: false, reason: "retired_session" };
        appendRetired(state.retiredSessions, state.session);
        clearSessionState(state, session, bootId);
        return { ok: true, changedSession: true };
    }
    if (state.boot_id !== bootId) {
        if (!switchBoot(state, bootId, wrappers))
            return { ok: false, reason: "retired_boot" };
        return { ok: true, changedBoot: true };
    }
    return { ok: true };
}

function responseBase(state) {
    return {
        v: VERSION,
        epoch: state.epoch,
        pid: state.pid,
        session: state.session,
        boot_id: state.boot_id
    };
}

function badBindResponse(state, error) {
    var result = responseBase(state);
    result.status = "unavailable";
    result.error = error || "invalid_request";
    return result;
}

function bindingResponse(state, request, token, status, error) {
    var result = responseBase(state);
    result.id = request.id;
    result.rev = request.rev;
    result.token = token || null;
    result.status = status;
    if (error)
        result.error = error;
    return result;
}

function tokenFor(state) {
    state.nextToken++;
    return state.epoch + "." + state.nextToken.toString(36);
}

function bind(state, parsed, wrappers) {
    if (!parsed || !parsed.ok)
        return { response: badBindResponse(state, parsed && parsed.error), record: null };
    var request = parsed.value;
    var entered = enterScope(state, request.session, request.boot_id, wrappers);
    if (!entered.ok)
        return { response: bindingResponse(state, request, null, "stale", entered.reason), record: null };

    var idKey = String(request.id);
    var record = state.records[idKey] || null;
    var lineage = state.lineages[idKey] || null;
    if (request.id <= state.releasedLocalIdHighWater && !lineage)
        return { response: bindingResponse(state, request, null, "stale", "released_local_id"), record: null };

    if (lineage && request.source_version < lineage.source_version)
        return { response: bindingResponse(state, request, null, "stale", "old_source_version"), record: record };
    if (lineage && request.source_version === lineage.source_version) {
        if (!record || record.source_version !== request.source_version || !record.active)
            return { response: bindingResponse(state, request, null, "stale", "source_version_revoked"), record: record };
        if (record.desktop_id !== request.desktop_id || !sameExpected(record.expected, request.expected))
            return { response: bindingResponse(state, request, null, "stale", "source_version_identity_changed"), record: record };
        var problem = recordProblem(record, wrappers);
        if (problem) {
            invalidate(state, record);
            return { response: bindingResponse(state, request, null, problem, "binding_no_longer_live"), record: record };
        }
        if (request.rev < record.rev)
            return { response: bindingResponse(state, request, null, "stale", "old_revision"), record: record };
        if (request.rev > record.rev) {
            record.rev = request.rev;
            lineage.rev = request.rev;
        }
        if (record.token && record.tokenLive && record.boot_id === state.boot_id) {
            return { response: bindingResponse(state, request, record.token, "ready"), record: record, duplicate: true };
        }
        if (!record.rebindable || record.boot_id !== state.boot_id)
            return { response: bindingResponse(state, request, null, "stale", "token_revoked"), record: record };
        record.token = tokenFor(state);
        record.tokenLive = true;
        record.rebindable = false;
        record.session = state.session;
        record.boot_id = state.boot_id;
        return { response: bindingResponse(state, request, record.token, "ready"), record: record, rebound: true };
    }

    if (lineage && request.source_version <= lineage.source_version)
        return { response: bindingResponse(state, request, null, "stale", "source_version_not_newer"), record: record };
    if (lineage && request.rev <= lineage.rev)
        return { response: bindingResponse(state, request, null, "stale", "revision_not_newer"), record: record };

    var candidate = inspectCandidate(wrappers, request);
    if (!candidate.ok)
        return { response: bindingResponse(state, request, null, candidate.status, candidate.reason), record: record };

    if (!lineage && Object.keys(state.lineages).length >= MAX_BINDINGS)
        return { response: bindingResponse(state, request, null, "unavailable", "lineage_capacity"), record: record };
    if (!record && Object.keys(state.records).length >= MAX_BINDINGS)
        return { response: bindingResponse(state, request, null, "unavailable", "binding_capacity"), record: null };

    if (record)
        invalidate(state, record);
    var next = {
        id: request.id,
        rev: request.rev,
        source_version: request.source_version,
        desktop_id: request.desktop_id,
        expected: {
            app: request.expected.app,
            summary: request.expected.summary,
            body: request.expected.body,
            default_label: request.expected.default_label
        },
        session: state.session,
        boot_id: state.boot_id,
        wrapper: candidate.wrapper,
        notification: candidate.notification,
        action: candidate.action,
        token: tokenFor(state),
        tokenLive: true,
        active: true,
        invalidated: false,
        rebindable: false
    };
    state.records[idKey] = next;
    state.lineages[idKey] = { source_version: request.source_version, rev: request.rev };
    return { response: bindingResponse(state, request, next.token, "ready"), record: next, created: true };
}

function status(state, wrappers) {
    var response = responseBase(state);
    var bindings = [];
    var ids = Object.keys(state.records);
    for (var i = 0; i < ids.length && bindings.length < MAX_BINDINGS; i++) {
        var record = state.records[ids[i]];
        if (!record || !record.token)
            continue;
        var problem = recordProblem(record, wrappers);
        if (problem)
            invalidate(state, record);
        var live = !!record.tokenLive && !problem && !!record.active;
        bindings.push({ id: record.id, rev: record.rev, token: record.token, live: live });
    }
    response.bindings = bindings;
    return response;
}

function release(state, parsed, wrappers) {
    if (!parsed || !parsed.ok) {
        var failed = responseBase(state);
        failed.status = "stale";
        failed.error = parsed && parsed.error ? parsed.error : "invalid_request";
        return { response: failed, record: null };
    }
    var request = parsed.value;
    var response = responseBase(state);
    response.id = request.id;
    response.rev = request.rev;
    response.token = request.token;
    response.final = request.final;
    response.status = "stale";
    if (state.session !== request.session || state.boot_id !== request.boot_id)
        return { response: response, record: null };
    var key = String(request.id);
    var record = state.records[key];
    if (!record || record.session !== request.session || record.boot_id !== request.boot_id
            || record.rev !== request.rev || record.token !== request.token) {
        return { response: response, record: null };
    }
    if (request.final) {
        invalidate(state, record);
        delete state.records[key];
        delete state.lineages[key];
        if (record.id > state.releasedLocalIdHighWater)
            state.releasedLocalIdHighWater = record.id;
        response.status = "released";
    } else {
        if (recordProblem(record, wrappers)) {
            invalidate(state, record);
        } else {
            record.tokenLive = false;
            record.rebindable = true;
        }
        response.status = "revoked";
    }
    return { response: response, record: record };
}

function activationResponse(state, request, status, error) {
    var response = responseBase(state);
    if (request) {
        response.session = request.session;
        response.boot_id = request.boot_id;
        response.id = request.id;
        response.rev = request.rev;
        response.request = request.request;
    }
    response.status = status;
    if (error)
        response.error = error;
    return response;
}

function sameRequestResult(a, b) {
    return !!a && a.session === b.session && a.boot_id === b.boot_id
        && a.id === b.id && a.rev === b.rev && a.token === b.token;
}

function rememberResult(state, request, response) {
    var key = String(request.request);
    state.recentResults[key] = { request: request, response: response };
    state.recentOrder.push(key);
    while (state.recentOrder.length > MAX_RECENT_RESULTS) {
        var removed = state.recentOrder.shift();
        delete state.recentResults[removed];
    }
}

function activate(state, parsed, wrappers, invokeAction) {
    if (!parsed || !parsed.ok)
        return activationResponse(state, null, "failed", parsed && parsed.error ? parsed.error : "invalid_request");
    var request = parsed.value;
    if (request.epoch !== state.epoch || request.session !== state.session || request.boot_id !== state.boot_id)
        return activationResponse(state, request, "stale", "scope_mismatch");

    var requestKey = String(request.request);
    var cached = state.recentResults[requestKey];
    if (cached) {
        if (sameRequestResult(cached.request, request))
            return cached.response;
        return activationResponse(state, request, "stale", "request_identity_mismatch");
    }
    if (request.request <= state.requestHighWater)
        return activationResponse(state, request, "stale", "old_request");

    state.requestHighWater = request.request;
    var response = activationResponse(state, request, "unknown");
    rememberResult(state, request, response);

    var record = state.records[String(request.id)];
    if (!record || record.session !== request.session || record.boot_id !== request.boot_id
            || record.rev !== request.rev || record.token !== request.token || !record.tokenLive || !record.active) {
        response.status = "stale";
        response.error = "binding_mismatch";
        return response;
    }
    var problem = recordProblem(record, wrappers);
    if (problem) {
        invalidate(state, record);
        response.status = problem;
        response.error = "binding_no_longer_live";
        return response;
    }
    if (typeof invokeAction !== "function") {
        response.status = "failed";
        response.error = "invoker_unavailable";
        return response;
    }
    try {
        invokeAction(record.action);
        response.status = "dispatched";
        delete response.error;
    } catch (e) {
        response.status = "unknown";
        response.error = "invoke_outcome_unknown";
    }
    return response;
}

function shouldWatch(record) {
    return !!record && !!record.active;
}
