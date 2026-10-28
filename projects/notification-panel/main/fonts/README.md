# Status fonts

`status_symbol_14.c` and `status_symbol_16.c` are the existing symbol fallback
fonts. `status_text_16.c`, `status_text_20.c` and `status_text_22.c` are standalone
composite fonts for notification bodies/titles. `status_clock_80.c` is a
Montserrat Medium font for digits, colon, and hyphen. The optional notification
body styles are `status_text_16_bold.c`, `status_text_16_italic.c` and
`status_text_16_bold_italic.c`. They contain only the Montserrat Latin and
punctuation glyphs selected below and each falls back to `status_text_16` for
CJK and the remaining symbols. All generated fonts use 4 bpp without
compression or kerning.

Each regular composite text font contains **8,327 glyphs**: the independently
pinned notification profile plus preserved legacy extras. The profile covers
the encoded Google Fonts Latin Core/Plus lists, CLDR 49 English and Simplified
Chinese punctuation/numbers, full Unicode Box Drawing, Block Elements and
Braille Patterns, and the first 6,500 characters (levels 1+2) of the 2013
通用规范汉字表. The [repertoire guide](repertoires/README.md) records the exact
sets, source versions, extraction, licenses and hashes.

Montserrat Medium supplies existing Latin/punctuation; DejaVu Sans supplies
existing symbols and terminal graphics; Source Han Sans SC supplies Chinese
and additional locale punctuation. Existing glyphs keep their original source.
New Bitcoin U+20BF uses Montserrat Medium from the same revision as the styled
fonts; lari U+20BE alone uses a pinned Noto Sans Medium instance. The 60 existing
FontAwesome private-use glyphs remain selected from LVGL's bundled WOFF.

Body styles retain their original Latin selection and fall back to regular
16 px for additional letters, currencies, terminal symbols and Chinese. Thus
all six font faces cover the required profile without changing accepted
styled metrics. The styles also retain the pre-existing U+2144 glyph, which is
not present in regular fonts; legacy-only extras need not be identical. The
bundled 14/16 px Source Han fallback chains used elsewhere in the UI retain
their original repertoire. Small app-name/age labels and rail metadata keep
the legacy font; the expanded profile applies to generated notification
title/body fonts.

This is character coverage, not full Google Fonts/OpenType shaping conformance.
Host projection retains its NFC and Latin accent-simplification policy. Supported
Unicode 17.0 FE0E/FE0F pairs use monochrome base symbols, such as `⚠️ → ⚠`.
Emoji sequences, kana and Han characters outside the pinned TGH and locale
sets remain outside the declared coverage; unsupported glyphs use LVGL
placeholders.

The preserved [level-1 list](repertoires/tgh-2013-level1.txt) contains Unicode
17.0.0 Unihan [`kTGH`](https://www.unicode.org/reports/tr38/tr38-39.html#kTGH)
indexes `2013:1`–`2013:3500`. The [expanded list](repertoires/tgh-2013-level1-plus2.txt)
contains indexes `2013:1`–`2013:6500`; the extractor checks the pinned archive
hash, requires every index once, sorts by index, and verifies that its first
3,500 characters match the preserved list. Both files use 100 UTF-8 characters
per line and a final newline. `kTGHZ2013` contains dictionary readings, not
table membership. Normal font regeneration uses the checked-in list offline.
The relevant SHA-256 values are:

- Unicode 17.0.0 archive: `f7a48b2b545acfaa77b2d607ae28747404ce02baefee16396c5d2d7a8ef34b5e`
- Preserved level-1 list: `4caca78057eb62257ed475e0a140b02a58c5907fd054613a93cfdec3a7df6320`
- Expanded levels-1+2 list: `825ecf0dbac8c66505fcee88e426b527a085d8936324ff4a9f9542b2f69b9a58`

Both lists are covered by [Unicode License V3](licenses/Unicode-LICENSE.txt).

The generated text fonts have these measured metrics:

| Font | Line height | Baseline | Bitmap data | C source | Compiled const |
| --- | ---: | ---: | ---: | ---: | ---: |
| `status_text_16` | 22 px | 5 px | 885,114 B | 6,427,800 B | 1,032,215 B |
| `status_text_20` | 26 px | 6 px | 1,369,538 B | 9,347,839 B | 1,516,639 B |
| `status_text_22` | 28 px | 6 px | 1,663,471 B | 11,099,258 B | 1,810,572 B |
| `status_clock_80` | 58 px | 1 px | 12,486 B | 78,566 B | 12,792 B |

Compiled const values use the fresh-default candidate build described below.

Including glyph descriptors and cmaps, the three regular notification fonts
occupy 1,032,215 B, 1,516,639 B and 1,810,572 B of compiled ESP32-S3 const
flash data, respectively. All linked fonts, including body styles, clock and
legacy UI fonts, contain 5,168,864 B of const flash data, plus 48 B of
initialized RAM descriptors in the legacy CJK fonts. The bitmap and lookup
tables are const flash data; their growth does not allocate an equivalent
amount of heap RAM.

A fresh-default firmware candidate built from base revision `a3a9127` with
`LV_FONT_FMT_TXT_LARGE=1` measured 5,888,784 B, leaving 2,499,824 B in the
8 MiB app partition. That is 1,938,880 B larger than the previous recorded
5,327-glyph candidate. This is a local build measurement; it has not been
deployed to a board.

The three 16 px body styles each contain 373 Montserrat glyphs, with
`line_height=22` and `base_line=5` to match `status_text_16`. Their bitmap data
is 22,669 B (bold), 23,547 B (italic), and 25,406 B (bold italic). Their C
sources are 186,377 B, 188,253 B, and 201,920 B; compiled const data are
28,919 B, 29,797 B, and 31,656 B, respectively. The generated
ink bounds are `[-5,+16]` vertically, within the 22/5 line metrics. Horizontal
ink overhang relative to rounded glyph advances reaches 4/4 px (bold), 4/5 px
(italic), and 5/5 px (bold italic) left/right; the maximum is U+2044 FRACTION
SLASH. For printable ASCII alone, the italic font reaches 3 px left overhang
at `j` and 3 px right overhang at `/`; bold italic reaches 3 px left at `j`
and 3 px right at `f`. A styled text area should reserve up to 5 px inside each
horizontal edge if it must display every selected glyph without clipping.

The widest valid 24-hour `HH:MM` string in `status_clock_80` is 233 px
(`04:44`), using LVGL's per-glyph advance rounding and no kerning. This leaves
room within the 456 px clock area.

The four source font files come from the LVGL component locked by
`dependencies.lock`. Their SHA-256 values and the legacy CJK reference hash
used to preserve existing Latin/symbol coverage and FontAwesome selections are:

- Montserrat-Medium.ttf: `421f26b23e2be6b98373d32acd3cb2897b154d4bf0a77d26534ce476e4cbed53`
- DejaVuSans.ttf: `3fdf69cabf06049ea70a00b5919340e2ce1e6d02b0cc3c4b44fb6801bd1e0d22`
- SourceHanSansSC-Normal.otf: `1ee89e1669362dee13851129c0a8a791a87521eb4148e5efbf5d26596738e25b`
- FontAwesome5-Solid+Brands+Regular.woff: `f4e42f6cd69e5dbdcccc0f2f5be136cebde0e427641e45403bf9173a92da95f4`
- `lv_font_source_han_sans_sc_16_cjk.c` repertoire reference: `4caaa2aff00bb59d1155227c838ced106ab4d61717afcaa66328cb0b3136785b`

The body style sources are official Montserrat Bold, Medium Italic and Bold
Italic TTFs from the
author repository at pinned commit
[`cc8daf2e7085006b9c112542fc82b58afc13521d`](https://github.com/JulietaUla/Montserrat/tree/cc8daf2e7085006b9c112542fc82b58afc13521d).
The matching SIL Open Font License text is kept beside them in
`assets/montserrat-cc8daf2/OFL.txt`.

- `Montserrat-Bold.ttf`: `bc6e854971cea46b463be6f9eef4d9cd52f51cfc1fc0dd90c9d3e6483dc0ec61`
- `Montserrat-MediumItalic.ttf`: `b544641c1fde7c5228a26ddade08870a9c1dc7426446828fc8aa8acab2521a45`
- `Montserrat-BoldItalic.ttf`: `b4c121b337aaa977d711b6f397c5b9672d720af622555f1e3f9d3b87f2983665`
- `OFL.txt`: `8b7141c03fa4f8d44e6345d5d4931709290f0f67875e452e95ac1fd3a027802e`

The matching `Montserrat-Medium.ttf` from the same `cc8daf2` revision supplies
only the newly required U+20BF Bitcoin sign; its SHA-256 is
`dae47428bb041f9716604e0e07b5b0c8585b3bdd8183362f75c69fe7bb3cfaf4`.
It uses the same adjacent OFL notice. U+20BE lari uses
[`NotoSans-Medium.ttf`](assets/noto-sans-ggfonts-ddf386b/README.md), SHA-256
`30202917c4a1f13275fc778e23320f6b3050e88c0e49c98dd3a0f8fdb7e4099e`;
that guide records its pinned upstream, weight/width instance, reproducible
derivation and OFL notice. Neither added source replaces an existing glyph.

`licenses/` contains the applicable regular Montserrat, DejaVu Sans, Source Han
Sans and FontAwesome license texts; the pinned Montserrat style license is alongside its source
assets. The font generator verifies the pinned input hashes, profile lists and source
assignment map, preserves legacy glyph coverage, and rejects missing or extra
output. It locks line heights/baselines and checks bitmap offsets against the
selected large firmware/native format's 32-bit descriptor limit. Normal
generation runs `lv_font_conv` 1.5.3
with npm's `--offline` option. Install/cache that tool once with network access
before regenerating on a new machine:

The 20/22 px fonts exceed the compact format's 20-bit bitmap offset. Fresh
firmware defaults and native tests select `LV_FONT_FMT_TXT_LARGE=1` and
`CONFIG_LV_FONT_FMT_TXT_LARGE=y`. An existing cached `sdkconfig` may retain the
old compact setting; update it to `CONFIG_LV_FONT_FMT_TXT_LARGE=y` before
building. Firmware has a compile-time guard for this setting.

```sh
npm exec --yes --package=lv_font_conv@1.5.3 -- lv_font_conv --help
```

Regeneration then uses checked-in font assets and repertoire data offline:

```sh
python tools/generate_status_fonts.py
python tools/generate_status_fonts.py --body-styles
python tools/generate_text_presentation.py
python tools/check_font_coverage.py
```

The coverage audit checks the legacy chains, exact 8,327-glyph regular
selection, independent required groups, source hashes, styled fallback,
matching metrics and ink bounds. It also checks the generated host
text-presentation table. The native fixture looks up every required profile
character through all six real LVGL font faces and renders terminal graphics,
numbers, mixed Chinese/English and styled specimens. Native and firmware use
the same large font descriptor format. Automated coverage and ink bounds do
not establish shaping or physical legibility; see the
[physical text checklist](../../docs/testing.md#notification-text-profile-acceptance).
