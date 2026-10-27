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

Each regular composite text font contains **5,327 glyphs**: the independently
pinned notification profile plus preserved legacy extras. The profile covers
the encoded Google Fonts Latin Core/Plus lists, CLDR 49 English and Simplified
Chinese punctuation/numbers, full Unicode Box Drawing, Block Elements and
Braille Patterns, and the existing 3,500 first-level characters of the 2013
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
Emoji sequences, kana and additional Han characters (including `帧` and `阈`)
remain outside the declared coverage; unsupported glyphs use LVGL placeholders.

The checked-in [Chinese character list](repertoires/tgh-2013-level1.txt) is
extracted from Unicode 17.0.0 [Unihan.zip](https://www.unicode.org/Public/17.0.0/ucd/Unihan.zip)
using the [`kTGH` property](https://www.unicode.org/reports/tr38/tr38-40.html#kTGH):
select `2013:1` through `2013:3500`, sort by table index, and write 100 characters
per UTF-8 line with a final newline. `kTGHZ2013` contains dictionary readings
and is not the table membership property. Regeneration uses the local list and
does not download Unihan. The relevant SHA-256 values are:

- Unicode 17.0.0 archive: `f7a48b2b545acfaa77b2d607ae28747404ce02baefee16396c5d2d7a8ef34b5e`
- Extracted list: `4caca78057eb62257ed475e0a140b02a58c5907fd054613a93cfdec3a7df6320`

The list is covered by [Unicode License V3](licenses/Unicode-LICENSE.txt).

The generated text fonts have these measured metrics:

| Font | Line height | Baseline | Bitmap data | C source |
| --- | ---: | ---: | ---: | ---: |
| `status_text_16` | 22 px | 5 px | 527,254 B | 3,867,286 B |
| `status_text_20` | 26 px | 6 px | 812,930 B | 5,582,538 B |
| `status_text_22` | 28 px | 6 px | 985,387 B | 6,608,325 B |
| `status_clock_80` | 58 px | 1 px | 12,486 B | 78,566 B |

Including glyph descriptors and cmaps, the three regular notification fonts
occupy 577,950 B, 863,626 B and 1,036,083 B of compiled ESP32-S3 flash data,
respectively. All linked fonts, including body styles, clock and legacy UI
fonts, contain 3,230,033 B (3.08 MiB) of const flash data, plus 48 B of initialized
RAM descriptors in the legacy CJK fonts. The firmware built with this
repertoire is 3,949,904 B (3.77 MiB), leaving 4.23 MiB in the current 8 MiB app partition.
Compared with the preceding box-drawing-only build, the firmware grows by
88,272 B (86.2 KiB). The bitmap and lookup tables are const flash data; their growth does not
allocate an equivalent amount of heap RAM.

The three 16 px body styles each contain 373 Montserrat glyphs, with
`line_height=22` and `base_line=5` to match `status_text_16`. Their bitmap data
is 22,669 B (bold), 23,547 B (italic), and 25,406 B (bold italic). Their C
sources are 186,377 B, 188,253 B, and 201,920 B, respectively. The generated
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
output. It locks line heights/baselines and checks the compact firmware font
format's 20-bit bitmap offsets. Normal generation runs `lv_font_conv` 1.5.3
with npm's `--offline` option. Install/cache that tool once with network access
before regenerating on a new machine:

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

The coverage audit checks the legacy chains, exact 5,327-glyph regular
selection, independent required groups, source hashes, styled fallback,
matching metrics and ink bounds. It also checks the generated host
text-presentation table. The native fixture looks up every required profile
character through all six real LVGL font faces and renders terminal graphics,
numbers, mixed Chinese/English and styled specimens. Native and firmware use
the same compact font descriptor format. Automated coverage and ink bounds do
not establish shaping or physical legibility; see the
[physical text checklist](../../docs/testing.md#notification-text-profile-acceptance).
