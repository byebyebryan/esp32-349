# UI design

[Project overview](../README.md) · [Behavior](behavior.md) · [Architecture](architecture.md)

The current `notification-history-v1` layout dedicates the display to recent
notifications beside a stable telemetry rail. Empty history says
`No recent notifications`; it does not return to a large-clock Home page.
Older grouped and horizontal-deck layouts are compatibility paths described
in [behavior](behavior.md#older-firmware-and-rendering).

## Geometry and hierarchy

The 640 × 172 landscape panel has a 160 px rail and 480 px content pane.
Notification cards are 464 × 140 px, including a 64 px control column at
their right edge. A singleton keeps the stack height and control positions,
so targets do not move when another notification arrives. The foreground
title/body/footer width is 376 px. A 22 px title and 16 px body show three
body lines; navigation moves whole cards rather than scrolling their text.

With actions enabled, Open sits above × in two 64 × 66 px controls separated
by 8 px. Without actions, × fills that same column. Open uses a vector box
with an outgoing arrow; × uses a large vector cross. Rounded 3 px strokes
stay visible without adding a symbol font. Pending Open shows an ellipsis;
disabled Open retains its own target and visibly muted treatment. Neighbor
previews hide controls and age metadata.

App name and relative age sit above the title. A neutral 4 px age track in
the footer normally spans card x=96–388, ending 12 px before the controls.
Long metadata can shorten it while keeping at least 80 px. Remaining time
is anchored at the right, so the bar empties left to right toward ×. It
shares the card's monotonic deadline and freezes with captured card content
during motion. It has no gesture or action of its own.

The rail uses a 40 px HH:MM clock and fixed CPU/MEM/UP/DN rows at y=52/84/116/136.
CPU/MEM each get 32 px including breathing room for a 136 × 4 px utilization
bar at x=12, y=76/108. Bars fill from the left; stale levels dim, unavailable
usage leaves an empty track. Labels start at x=12, details occupy x=52–110,
and percentages x=110–148. Values align right to hold units in place.
`99%` reserves a space; `100%` uses 14 px on the same baseline. Traffic spans
x=52–148 and uses a space before B/s, KiB/s, MiB/s or GiB/s. The rail has no
battery row. Sampling and missing-value rules are in [behavior](behavior.md#telemetry).

## Color and typography decisions

The palette uses neutral gray surfaces and one muted sage clock accent.
Uniform numbers and units make a reading scan as one value; per-row colors
and mixed number/unit colors proved visually busy. Titles lead, body text
is quieter, metadata is secondary. Critical urgency colors the sender and
left stripe without turning the age bar into another urgency signal. Open
has a restrained action tint, with distinct pressed/disabled states.

| Role | RGB color |
| --- | --- |
| Background / rail / card | `#181818` / `#212121` / `#282828` |
| Button / pressed / divider | `#333333` / `#404040` / `#424242` |
| Primary / body / secondary text | `#E6E6E6` / `#C8C8C8` / `#A8A8A8` |
| Clock and sender sage / live rail values | `#BBD1AB` / `#D4D4D4` |
| Accent / warning / critical | `#AFC19E` / `#D4BB8D` / `#D79A9F` |
| Open fill / border / pressed | `#30382F` / `#536150` / `#405044` |
| Disabled Open fill / border / glyph | `#242424` / `#383838` / `#929292` |

[ui_theme.h](../main/ui_theme.h) is the source of color values. Native RGB565
captures check rendering, not measured panel contrast. Fonts are generated
from Montserrat with regular CJK/symbol fallback; only the Latin body has
bold/italic variants. See [fonts and licenses](../main/fonts/README.md).

## Gesture and motion ownership

A press captures one owner: Open, × or card navigation. Button targets gain
8 px padding, with the initial gap split at its midpoint so only one button
can own it. A captured button stays armed inside its original padded rectangle;
there is no extra radial movement cutoff. Leaving that rectangle or crossing
the other visible button cancels it for the remainder of the contact.
Reset, card removal and an Open revision change also cancel. Disabled/pending
Open consumes its press; a body-started drag cannot turn into a button action.
Pressed feedback is applied only to accepted contacts rather than LVGL's
automatic pressed state.

Vertical navigation has bounds and advances one card. Short drags return;
deliberate flicks advance. Horizontal/body taps are inert. Gesture snapshots
copy ID, text, styles and deadlines; disappearance of source/destination
cancels the transition instead of selecting by a stale array position.
Manual browsing keeps its selected ID across normal arrivals. After 30 seconds
without interaction, later arrivals may focus again; critical attention is
validated after the current gesture settles.

Arrivals and dismissal use 180 ms eased motion across three reused card
widgets. A newer history card enters from above; dismissal moves the outgoing
card up and the surviving card into place. State removal and host input happen
immediately; an outgoing widget merely retains pixels and cannot write back
or accept input. Touches begun during lifecycle animation stay inert until
release. Same-ID replacements and full sync update settled content without
replaying an entrance. Normal arrival focus is deferred or discarded during
interaction. Motion adds no framebuffer, image or font assets and allocates
no widgets per frame. The shared full-frame transfer constraint remains;
see [rendering](architecture.md#clock-and-rendering-constraints).

Current synthetic previews and their reproducible provenance belong in
[project media](media/README.md). Physical checks and their limitations are
recorded in [STATUS.md](../STATUS.md) and the [testing guide](testing.md).
