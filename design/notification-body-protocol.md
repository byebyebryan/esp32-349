# Notification body styles v1

`notification-body-style-v1` requires `notification-history-v1` and its complete
cache/dashboard/grouped capability set. The host negotiates support on each
hello and removes style fields for every older peer projection, including
incremental notifications, chunked sync and legacy sync. Protocol version
remains 1; `body` always contains the complete readable plain-text fallback.

Optional `body_runs` on a history notification or sync card:

```json
{"body":"Build passed.\nReview changes.","body_runs":[{"start":0,"end":13,"style":1}]}
```

| Field | Meaning |
|---|---|
| `start` | Inclusive UTF-8 byte offset in the final normalized body |
| `end` | Exclusive UTF-8 byte offset, greater than `start` |
| `style` | 1 bold, 2 italic, 3 bold and italic |

Each range has exactly these three integer fields. Offsets must be Unicode
code point boundaries within the body. At most 16 ranges are allowed, ordered
and nonoverlapping. Gaps use the normal body font. The host merges adjacent
equal ranges and keeps clipping ellipses regular. Normalization precedes range
calculation; the existing 511-byte history body limit includes line breaks and
any text ellipsis. Formatting metadata does not reduce that text budget.

Host display conversion processes at most 8,192 Unicode code points from a
body before HTML/Markdown parsing or Unicode/style normalization. Longer
sources keep a bounded prefix and a regular ellipsis, including when markup
or whitespace removal leaves a short visible body. This processing bound is
separate from the final 511-byte display limit and does not alter the original
D-Bus strings used for Open validation.

Missing, empty, malformed or excessive ranges clear styling and retain the
valid plain notification. Firmware validates all ranges before using any.
Replacing a styled card with a plain card removes old styling. Metadata travels
with the cached text and every UI snapshot; no independent redraw can renew
history or change action identity. Full sync keeps its existing staging and
atomic commit rules. Actual encoded lines retain the 2048-byte soft chunk target
and 8192-byte hard limit, with oversized valid singleton chunks allowed.

The device stores 16 fixed eight-byte ranges plus a count per notification.
Only visible card bodies allocate LVGL spans; they are rebuilt on content
changes, not gesture frames. Styled bodies retain the fixed body rectangle,
neutral color and 16 px size. Montserrat bold/italic variants cover Latin and
punctuation and fall back to the existing regular CJK/symbol font. No HTML or
Markdown parser runs on the ESP32, and body links remain inert.

Display conversion uses a separate representation from the original D-Bus
app/title/body kept for Open validation. Source classification and producer
paragraph limits are described in the [implementation plan](notification-body-plan.md).
