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

Each regular composite text font contains 4,844 glyphs focused on English and
Simplified Chinese notifications. Montserrat Medium supplies the existing
ASCII, Latin-1/Extended-A, and punctuation coverage; DejaVu Sans supplies the
existing arrow, math, technical, shape, and dingbat ranges. Source Han Sans SC
supplies the 3,500 first-level characters of the 2013 通用规范汉字表, plus 20
Chinese punctuation characters: `　、。，！？：；（）【】《》〈〉「」『』`.
Typographic quotes and the ellipsis come from Montserrat. Kana and Han
characters outside that Chinese table are omitted from these notification
fonts. Shared Han characters naturally remain available in other languages.
The 60 existing FontAwesome private-use glyphs are selected from LVGL's bundled
WOFF as a fourth source. Unsupported emoji continue to use LVGL's placeholder.
The bundled 14/16 px Source Han fallback chains used elsewhere in the UI retain
their original repertoire.

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
| `status_text_16` | 22 px | 5 px | 500,991 B | 3,651,555 B |
| `status_text_20` | 26 px | 6 px | 774,276 B | 5,294,429 B |
| `status_text_22` | 28 px | 6 px | 939,182 B | 6,273,990 B |
| `status_clock_80` | 58 px | 1 px | 12,486 B | 78,566 B |

Including glyph descriptors and cmaps, the three regular notification fonts
occupy 547,621 B, 820,906 B and 985,812 B of compiled ESP32-S3 flash data,
respectively. All linked fonts, including body styles, clock and legacy UI
fonts, contain 3,106,713 B (2.96 MiB) of const flash data, plus 48 B of initialized
RAM descriptors in the legacy CJK fonts. The firmware built with this
repertoire is 3,820,944 B (3.64 MiB), leaving 4.36 MiB in the current 8 MiB app partition.
The bitmap and lookup tables are const flash data; their growth does not
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

`licenses/` contains the applicable regular Montserrat, DejaVu Sans, Source Han
Sans and FontAwesome license texts; the pinned Montserrat style license is alongside its source
assets. The font generator verifies the pinned input hashes and Chinese list,
preserves the old 14/16 union's Latin/symbol coverage, replaces its CJK selection
with the Chinese table and punctuation, and rejects output that differs from
that exact selection. Body styles have a
separate mode so they can be regenerated without touching any existing regular
composite font. It uses `lv_font_conv` 1.5.3:

```sh
python tools/generate_status_fonts.py
python tools/generate_status_fonts.py --body-styles
python tools/check_font_coverage.py
```

The coverage audit checks the 14 px and 16 px legacy chains' Latin/symbol
coverage, the exact English/Simplified Chinese selection at 16 px, 20 px and
22 px, all 3,500 Chinese characters and 20 punctuation glyphs, and realistic
English and Chinese notification samples. It also checks styled-font glyph coverage,
the regular fallback pointer, matching body metrics, vertical ink bounds and
horizontal overhang, plus that the 80 px clock has only digits, colon, and
hyphen. The audit checks generated glyph metrics, not shaping or physical
legibility. The native LVGL tests check actual glyph lookup for formerly
missing Chinese characters, punctuation, and the styled-body fallback, and
render mixed English/Simplified Chinese notification cards.
