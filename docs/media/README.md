# Project gallery media

Application screenshots and animations live with their owning project:

| Project | Media and regeneration |
| --- | --- |
| notification-panel | [Native hero, screenshots and gesture demo](../../projects/notification-panel/docs/media/README.md) |
| render-bench | [Native bouncing-ball scene and animation](../../projects/render-bench/docs/media/README.md) |

The root gallery follows the same format for each project: a preview, a short
description and links to the project, behavior/findings and media source.
Animations share a 640 × 172 screen area and a caption band identifying native
rendering. The source pages keep playback timing separate from device results.

For new projects, place selected assets and their regeneration instructions in
`projects/<name>/docs/media/`. Include a source/output manifest for generated
native media; the [shared documentation check](../../tools/check_docs.py)
discovers manifests across the collection. The [development guide](../development.md#add-a-project)
describes the manifest paths and application boundaries.

Label synthetic renders and staged animation timing. Device photographs and
videos should identify the project, board revision, firmware build and capture
date. Keep original footage and raw captures outside Git; publish selected,
compressed assets. Physical observations belong in that project's acceptance
record.
