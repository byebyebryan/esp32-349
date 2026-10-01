# Notification body cleanup and limited formatting

Design and quick feasibility review, 2026-09-30. Both checkpoints are now
implemented and passed Starship's short physical check. See the separate
[acceptance record](notification-body-acceptance.md) for deployed hashes,
automated evidence and remaining limits, and the
[wire contract](notification-body-protocol.md) for the settled metadata format.

## Direction

Deliver readable plain text first, then optional **bold**, *italic* and useful
line breaks in the existing notification body. Keep the fixed rail, 16 px body,
fixed card height, vertical card navigation, inert body taps and explicit
Open/× controls. Retention remains 600 seconds from arrival/replacement, capped
at 32 cards. Additional formatting must not change action identity or lifetime.

Inline code gets readable text with no special font or background. Links keep
their visible label; images contribute alternative text when present. These
are content-preservation rules, not new interactive elements. Underline,
formatting colors, images, tables and body-text scrolling are outside this pass.

## Pre-implementation baseline

- Chrome's Linux notification bridge can add an origin hyperlink, escape
  message text and format list-item titles. Starship's desktop advertises
  `body-markup`, `body-hyperlinks` and `body-images`.
- Codex's local `codex-notify` helper forwards `last-assistant-message` as
  plain terminal text. Markdown markers survive; newlines are flattened by
  the helper before Kitty/Ghostty receive the message.
- Kitty's notification API takes plain UTF-8 or base64 text, not HTML or
  Markdown. Its installed Linux adapter inserts U+200C after `<` and `&` to
  prevent desktop markup interpretation. Those guards are not display content.
- `sources/notifications.py` clips bodies to 511 UTF-8 bytes at intake.
  `proto.display_text()` turns CR/LF/tab into spaces, and subsequent notify,
  projection and sync serialization repeat that normalization.
- The firmware stores a 512-byte body buffer and draws a plain LVGL label.
  Body text already supports explicit LF when supplied directly on the wire.
- `parse_open_metadata()` independently stores the original app/title/body
  for DMS action validation. Keep that representation separate from display text.

Sources: [notification markup specification](https://specifications.freedesktop.org/notification/latest/markup.html),
[Chromium Linux notification bridge](https://raw.githubusercontent.com/chromium/chromium/main/chrome/browser/notifications/notification_platform_bridge_linux.cc),
[Kitty notification protocol](https://sw.kovidgoyal.net/kitty/desktop-notifications/).
Installed helper, adapter and repo code establish the local behavior above;
upstream code is not a capture of a particular Chrome/Slack notification.

## Conversion and ownership

```text
Original desktop notification
  ├─ original metadata → existing Open validation/correlation
  └─ source-aware body conversion, before clipping
       → normalized display text, preserving body line breaks
       → optional bounded emphasis ranges
       → peer-specific notify/full-sync projection
       → existing label initially; LVGL spans in checkpoint 2
```

1. Add a host module, proposed `host/src/status349/notification_text.py`.
   One conversion path serves incremental arrivals/replacements and full sync.
   All HTML/Markdown parsing runs on the PC; the ESP32 receives plain text and
   later a bounded list of style ranges.
2. Interpret the supported desktop markup subset with a parser, not a tag
   removal regex. Decode entities once while parsing; escaped literal tags
   must stay literal. Strip recognized formatting wrappers, retain link labels
   and image alt text, and use LF for existing/recognized structural breaks.
   Preserve unknown literal constructs such as `<T>` instead of deleting them.
3. Parse Markdown only for positively identified Codex notifications: known
   terminal sender plus the helper's exact `Codex` title. Prefer `desktop-entry`
   identification where available. Other terminal notifications stay plain;
   do not treat every underscore, asterisk or bracket as formatting.
4. For Kitty text, remove only its inserted guards immediately after `<`/`&`,
   and preserve the resulting text literally rather than interpreting it as
   HTML. Keep code contents and paths intact during Markdown conversion;
   remove code delimiters without assigning code styling.
5. Use a maintained Markdown parser for the identified source if needed;
   do not build a full Markdown grammar with regexes. Select and lock the
   dependency during checkpoint 1, using only the needed token conversion.
6. Normalize Unicode and whitespace before deriving final UTF-8 byte ranges.
   Preserve LF in notification bodies through every projection; keep existing
   single-line normalization for app/title, telemetry and legacy zones.
   Collapse excessive blank lines, omit leading/trailing blank lines, and
   preserve punctuation/CJK under the current font policy.
7. Clip the converted body once to the existing 511-byte bound with ellipsis.
   Repeated serialization must be idempotent. Legacy projections retain their
   159-byte bound. Formatting must not consume the plain-text content budget.
8. Keep original Open metadata untouched. Replacement, expiration, cached-card
   selection and action checks continue to use their existing identities and
   receipt times. Adding formatting must not synthesize a replacement or renew
   retention just to redraw a card.

### Codex paragraph delivery

Do not strip Markdown in the producer helper: that would permanently discard
the emphasis needed in checkpoint 2. Convert only the device display projection.

For Kitty, change the helper's base64 body branch to preserve normalized LF,
while still removing terminal escape/control bytes and retaining its UTF-8
size bound, focus filtering, tmux wrapping and native default action. The
base64 envelope keeps OSC control delimiters out of the payload transport.

The Ghostty OSC 777 branch keeps its current compact fallback initially.
Paragraph delivery there needs its own bounded transport check; readable text
and inline emphasis can still work from the compact body. Do not replace native
terminal notifications with direct D-Bus emission or introduce a duplicate
notification stream just to obtain paragraphs.

Do not use U+2028 as a universal paragraph substitute: the local Qt probe
preserved it in plain text but collapsed it in rich text. A remaining Ghostty
paragraph gap must be reported explicitly, not called full terminal parity.

## Checkpoint 1 — clean text and available line breaks

Ownership:

- Host: notification text conversion, source identification, intake ordering,
  body-specific normalization and all notify/sync/legacy projections.
- Chezmoi: `dot_local/bin/executable_codex-notify` and
  `scripts/check-codex-notify` for Kitty's preserved base64 LF. Change the
  managed source first; deploy to the relevant hosts only during rollout.
- Firmware: no source change expected. Existing labels accept plain LF.

Sequence:

1. Establish fixtures for Chrome links/entities, supported desktop tags,
   literal escaped tags, unknown `<T>`, Kitty guards, Codex emphasis, ordinary
   Claude/terminal messages, identifiers/code contents and English/CJK.
2. Implement source-aware conversion before current intake truncation.
   Expose a plain-text result first; keep parser structure suitable for later
   emphasis extraction without changing the desktop notification body.
3. Preserve Kitty LF in the helper and notification-body LF across all host
   projections. Keep the Ghostty branch's explicit compact fallback.
4. Verify action binding receives the exact original metadata while the
   card receives the converted body, including replacement and reconnect.
5. Run focused host/helper checks and composed native history/action scenarios.
   Compare the plain fallback on older peers and line breaks on current firmware.
6. Reload/restart only as required to load changed host code, retaining the
   documented RAM-history restart behavior. Apply the helper from chezmoi.
   No firmware flash is expected for this checkpoint.

Exit: Chrome formatting debris is gone; identified Codex Markdown is readable;
literal code/identifiers and existing actions survive; paragraphs available at
intake survive serialization. Record the Ghostty paragraph limit explicitly.

## Checkpoint 2 — bounded bold and italic

Use the same source conversion to derive emphasis from desktop `<b>/<i>` and
identified Codex Markdown. Use the body only; app/title/age retain their current
visual hierarchy. Adjacent equal styles merge, nested emphasis becomes the
combined style, and malformed/excessive styling falls back to readable plain text.

### Proposed wire contract

Add a capability such as `notification-body-style-v1`, usable only with the
existing history/cache capability set. Extend both notify and sync cards with
optional `body_runs`; `body` always remains a complete plain fallback:

```json
{
  "body": "Build passed.\nReview changes.",
  "body_runs": [{"start": 0, "end": 13, "style": 1}]
}
```

- Offsets are UTF-8 byte offsets into the final normalized/clipped `body`, with
  an exclusive end. Style flags: `1` bold, `2` italic, `3` both; gaps are regular.
- At most 16 ordered, nonoverlapping ranges, each nonempty and ending/starting
  on UTF-8 boundaries. Calculate offsets after NFC/accent handling and clipping;
  test combining marks and keep the appended ellipsis regular.
- If the range limit is exceeded, send the complete bounded plain body with no
  styles. Keep the existing 32-card and 511-byte limits.
- Older peers receive only plain text. Never leak new fields via retained model
  snapshots or an alternate serialization path. Incremental/full-sync results
  must agree, and unavailable/invalid styling must clear any previous styles.
- The firmware validates metadata independently; invalid style data degrades
  to plain text for an otherwise valid card rather than losing that notification.
- Measure actual JSON escaping and preserve existing chunk splitting plus the
  singleton fallback below the 8192-byte hard line limit.

### Fonts and widgets

1. First prove a fixed-width/height LVGL span body with regular, bold, italic
   and combined styles, line breaks and ellipsis. Use the existing native
   runner; test fonts and captures before integrating gestures.
2. Acquire licensed, pinned Montserrat-family variants matching the current
   body appearance. Generate only 16 px Latin/punctuation variants and fall
   back to `status_text_16` for CJK/symbols. CJK/symbol fallback stays regular
   in this first formatting pass; do not duplicate the full CJK font set.
   Check bold/italic Latin coverage, baseline, line height and italic overhang.
3. Preserve the current body rectangle and line budget. Reuse the accepted
   neutral body color; emphasis is typography, not additional colors.
4. Store ranges in bounded cached-card records and carry them through every
   deck/lifecycle snapshot. Audit copying, allocation and stack use; update
   native build/font manifests along with production sources.
5. Make spans inert and preserve the body's gesture/event route. Rebuild
   spans on content changes, not on every drag frame. Arrival, replacement,
   dismiss, held gesture and offline expiry must use a coherent captured body
   plus style snapshot.

Exit: styling and clipping are predictable, plain compatibility works, normal
swipes/Open/× and history recovery pass automated integration, firmware builds,
and a brief on-device check accepts readability and motion.

## Validation and rollout

| Gate | Required evidence |
|---|---|
| Host conversion | Entities decoded once, source classification, literal preservation, malformed input fallback, normalization/clipping idempotence, combining characters and size bounds |
| Producer helper | Encoded Kitty LF and control sanitization, existing focus/tmux behavior, UTF-8 limits; Ghostty fallback explicitly checked |
| Action identity | Raw body/title/app sent to DMS binding, converted display body kept separate; replacement/close/Open regressions |
| Wire/C state | Capability negotiation, invalid-range fallback, UTF-8 boundaries, chunk limits, atomic sync, style removal on plain replacements |
| Native LVGL | Production fonts, multiline/ellipsis captures, nested styles and CJK fallback, automated pointer/lifecycle/history cases; Debug and Release |
| Target build | Binary/font size and stack/heap comparison to the current source baseline; no new frame-rate target or general performance claim |
| Physical | One 2–3 minute session: representative Chrome/Codex bodies, bold/italic readability, a few swipes, × and one real Open action |
| Closure | Exact source/binary/host/helper provenance, normal mirroring restored, owned test cards cleared, source/deployed parity recorded |

Prefer automated tests for expiry, held-gesture races and reconnect. Do not
repeat the earlier involved manual hold/inject sequences for this feature.

Implement and accept checkpoint 1 before checkpoint 2. Keep host/helper changes,
firmware/formatting changes, fonts and acceptance docs reviewable. Commit/push
remain separate user-requested steps. Maintain clean-text fallback for rollback.

## Quick plan validation

These are planning probes, not implementation acceptance or panel observations.
Scratch evidence is under `/tmp/esp32-349-notification-plan-6stmx0tu` and is
temporary; implementation must produce its own reproducible checks.

- The actual installed Kitty sanitizer preserved `First\nSecond` after a
  base64 encode/decode round trip, while removing ESC/BEL. The earlier concern
  that all terminal notification paths must flatten paragraphs was too broad.
- Qt offscreen text measured two lines for LF and U+2028 in plain text, but one
  for a rich-text U+2028 example. Reject universal Unicode-separator substitution.
- The existing composed native runner accepted LF in a negotiated history card
  and rendered a different body from the otherwise identical flattened text.
  This proves native parser/label feasibility, not device physical readability.
- A 511-byte escaped body plus 16 proposed style ranges serialized to 1,738 B
  for notify and 1,785 B for a singleton sync card, below the current limits.
  This is one sizing probe; hostile escaping/batch cases belong in implementation
  tests, and current firmware does not implement those proposed ranges.
- Budgeting eight bytes per fixed range gives 4 KiB across 32 cached cards,
  excluding deck copies and temporary spans. Actual ABI sizes, font growth and
  on-device resource effects still need measurement.
- Production LVGL span support is enabled; the native configuration defaults
  to enabled too. Existing span APIs provide fixed size, font styles and ellipsis.
  There are no current generated bold/italic body fonts, so font/layout proof is
  the first gate of checkpoint 2, not an assumed completed capability.
- Open validation already has a distinct unnormalized representation, making
  display-only conversion possible without changing the desktop bridge contract.

### Effort and remaining decisions

Rough engineering estimate: checkpoint 1 about 2–4 hours, paragraph/helper work
about 1–2 hours, limited styling about 4–8 hours, plus integration/physical
acceptance: approximately 1–2 focused workdays. This assumes regular CJK fallback
and no new delivery channel. Font metric problems or a requirement for full
Ghostty paragraph parity can extend that estimate.

Implementation chooses the pinned Markdown parser and exact font assets after
their small fixture/metric checks. The behavior, conversion boundary, cache
budget, compatibility strategy and acceptance scope above are ready to execute.
