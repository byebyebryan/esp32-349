# Collection and project media

The repository header, [collection.svg](collection.svg), introduces the hardware
and shared project structure. It is editable vector artwork, not a device
photograph or a render of an application's UI.

Application screenshots and animations live with their owning project:

| Project | Media and regeneration |
| --- | --- |
| notification-panel | [Native hero, screenshots and gesture demo](../../projects/notification-panel/docs/media/README.md) |
| render-bench | [Recorded hardware and rendering findings](../../projects/render-bench/docs/rendering.md); no published demo media yet |

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
