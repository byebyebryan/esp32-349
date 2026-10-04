# Legacy host paths

The host application now lives in
[`projects/notification-panel/host`](../projects/notification-panel/host).
Use the [project setup guide](../projects/notification-panel/README.md#host-setup)
for new installations, development and tests.

This directory contains compatibility links for existing service symlinks,
`uv run --project host 349d` / `349ctl` invocations and editable Python installs.
It remains a directory so a Git update preserves the existing ignored
`.venv/` and other local files. Each link points to the project's maintained
file or source directory; there is no second copy of the application here.
