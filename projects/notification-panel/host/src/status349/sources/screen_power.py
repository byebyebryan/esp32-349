"""Read Linux DRM monitor power without requiring a desktop-session socket."""

from pathlib import Path
import re


class ScreenPowerSource:
    def __init__(self, root: Path = Path("/sys/class/drm")):
        self.root = root

    def read(self) -> dict:
        outputs = []
        unknown = False
        try:
            connectors = sorted(self.root.iterdir())
        except OSError as exc:
            return {"on": None, "outputs": [], "error": str(exc)}
        for connector in connectors:
            if not re.fullmatch(r"card\d+-(?!Writeback-).+", connector.name):
                continue
            try:
                status = (connector / "status").read_text().strip()
                if status == "disconnected":
                    continue
                if status != "connected":
                    unknown = True
                    continue
                enabled = (connector / "enabled").read_text().strip()
                dpms = (connector / "dpms").read_text().strip()
            except OSError:
                # Hotplug can remove a connector between these reads. Never
                # turn an I/O error into a claim that all screens are off.
                unknown = True
                continue
            outputs.append({"name": connector.name, "enabled": enabled, "dpms": dpms})
            if enabled not in {"enabled", "disabled"} or dpms not in {"On", "Standby", "Suspend", "Off"}:
                unknown = True
        if any(output["enabled"] == "enabled" and output["dpms"] == "On" for output in outputs):
            on = True
        elif outputs and not unknown:
            on = False
        else:
            on = None
        return {"on": on, "outputs": outputs, "error": "monitor state unavailable" if unknown else None}
