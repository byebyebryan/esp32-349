import QtQuick
import Quickshell
import Quickshell.Io
import qs.Services
import qs.Modules.Plugins
import "ProviderCore.js" as Core

PluginComponent {
    id: root

    readonly property string epoch: "dms-" + Quickshell.processId + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12)
    property var provider: Core.createState(epoch, Quickshell.processId)
    property var watchers: []
    property bool hasLineages: false

    function updateActivity() {
        var active = false;
        var records = provider.records;
        var ids = Object.keys(records);
        for (var i = 0; i < ids.length; i++) {
            if (Core.shouldWatch(records[ids[i]])) {
                active = true;
                break;
            }
        }
        hasLineages = active;
        watcherCleanup();
    }

    function watchRecord(record) {
        if (!Core.shouldWatch(record))
            return true;
        for (var i = 0; i < watchers.length; i++) {
            if (watchers[i].record === record)
                return true;
        }
        var notificationConnection = notificationConnections.createObject(root, {
            "record": record
        });
        var actionConnection = actionConnections.createObject(root, {
            "record": record
        });
        if (!notificationConnection || !actionConnection) {
            if (notificationConnection)
                notificationConnection.destroy();
            if (actionConnection)
                actionConnection.destroy();
            Core.invalidate(provider, record);
            return false;
        }
        watchers = watchers.concat([{
            "record": record,
            "notification": notificationConnection,
            "action": actionConnection
        }]);
        return true;
    }

    function watcherCleanup() {
        var keep = [];
        for (var i = 0; i < watchers.length; i++) {
            var watcher = watchers[i];
            var current = provider.records[String(watcher.record.id)];
            if (current === watcher.record && Core.shouldWatch(watcher.record)) {
                keep.push(watcher);
            } else {
                watcher.notification.destroy();
                watcher.action.destroy();
            }
        }
        watchers = keep;
    }

    function invalidate(record) {
        Core.invalidate(provider, record);
        watcherCleanup();
        updateActivity();
    }

    function refresh() {
        Core.status(provider, NotificationService.allWrappers);
        updateActivity();
    }

    function statusJson(): string {
        var response = Core.status(provider, NotificationService.allWrappers);
        updateActivity();
        return JSON.stringify(response);
    }

    function bindJson(payload: string): string {
        var parsed = Core.parseBindPayload(payload);
        var result = Core.bind(provider, parsed, NotificationService.allWrappers);
        if (result.record && result.response.status === "ready" && !watchRecord(result.record)) {
            result.response.token = null;
            result.response.status = "unavailable";
            result.response.error = "watch_registration_failed";
        }
        updateActivity();
        return JSON.stringify(result.response);
    }

    function activateJson(payload: string): string {
        var parsed = Core.parseActivatePayload(payload);
        var response = Core.activate(provider, parsed, NotificationService.allWrappers, function (action) {
            action.invoke();
        });
        updateActivity();
        return JSON.stringify(response);
    }

    function releaseJson(payload: string): string {
        var parsed = Core.parseReleasePayload(payload);
        var result = Core.release(provider, parsed, NotificationService.allWrappers);
        updateActivity();
        return JSON.stringify(result.response);
    }

    Component {
        id: notificationConnections

        Connections {
            property var record: null
            target: record ? record.notification : null
            ignoreUnknownSignals: true

            function onClosed(reason) {
                root.invalidate(record);
            }

            function onTrackedChanged() {
                root.invalidate(record);
            }

            function onAppNameChanged() {
                root.invalidate(record);
            }

            function onSummaryChanged() {
                root.invalidate(record);
            }

            function onBodyChanged() {
                root.invalidate(record);
            }

            function onActionsChanged() {
                root.invalidate(record);
            }
        }
    }

    Component {
        id: actionConnections

        Connections {
            property var record: null
            target: record ? record.action : null
            ignoreUnknownSignals: true

            function onTextChanged() {
                root.invalidate(record);
            }
        }
    }

    Connections {
        target: NotificationService
        ignoreUnknownSignals: true

        function onAllWrappersChanged() {
            root.refresh();
        }
    }

    Timer {
        interval: 1000
        repeat: true
        running: root.hasLineages
        onTriggered: root.refresh()
    }

    IpcHandler {
        target: "349-notification-actions"

        function status(): string {
            return root.statusJson();
        }

        function bind(payload: string): string {
            return root.bindJson(payload);
        }

        function activate(payload: string): string {
            return root.activateJson(payload);
        }

        function release(payload: string): string {
            return root.releaseJson(payload);
        }
    }

    Component.onCompleted: console.info("349 notification action provider started", epoch, Quickshell.processId)
    Component.onDestruction: console.info("349 notification action provider stopped", epoch)
}
