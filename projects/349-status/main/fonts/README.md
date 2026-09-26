# Status fonts

`status_symbol_14.c` and `status_symbol_16.c` are the existing symbol fallback
fonts. `status_text_20.c` and `status_text_22.c` are standalone composite fonts
for the larger UI text. `status_clock_80.c` is a Montserrat Medium font for
digits, colon, and hyphen. All five use 4 bpp without compression or kerning.

Each generated text font preserves the complete union of glyphs from the old
14 px and 16 px Montserrat, Source Han, and symbol font chains. Montserrat
Medium supplies ASCII, Latin-1/Extended-A, and punctuation; DejaVu Sans supplies
the existing arrow, math, technical, shape, and dingbat ranges. Source Han Sans
supplies exactly the non-ASCII CJK repertoire identified by the `U+` comments
in LVGL's `lv_font_source_han_sans_sc_16_cjk.c`. ASCII is omitted from that
Source Han selection because Montserrat supplies it. The reference C file also
contains 60 existing FontAwesome private-use glyphs, so those same codepoints
are selected from LVGL's bundled FontAwesome WOFF as a fourth source. No other
icon codepoints are added. Unsupported emoji continue to use LVGL's placeholder.

The generated text fonts have these measured metrics:

| Font | Line height | Baseline | Bitmap data | C source |
| --- | ---: | ---: | ---: | ---: |
| `status_text_20` | 26 px | 6 px | 355,688 B | 2,472,051 B |
| `status_text_22` | 28 px | 6 px | 430,110 B | 2,913,398 B |
| `status_clock_80` | 58 px | 1 px | 12,486 B | 78,566 B |

The widest valid 24-hour `HH:MM` string in `status_clock_80` is 233 px
(`04:44`), using LVGL's per-glyph advance rounding and no kerning. This leaves
room within the 456 px clock area.

The four source font files come from the LVGL component locked by
`dependencies.lock`. Their SHA-256 values and the CJK repertoire reference
hash are:

- Montserrat-Medium.ttf: `421f26b23e2be6b98373d32acd3cb2897b154d4bf0a77d26534ce476e4cbed53`
- DejaVuSans.ttf: `3fdf69cabf06049ea70a00b5919340e2ce1e6d02b0cc3c4b44fb6801bd1e0d22`
- SourceHanSansSC-Normal.otf: `1ee89e1669362dee13851129c0a8a791a87521eb4148e5efbf5d26596738e25b`
- FontAwesome5-Solid+Brands+Regular.woff: `f4e42f6cd69e5dbdcccc0f2f5be136cebde0e427641e45403bf9173a92da95f4`
- `lv_font_source_han_sans_sc_16_cjk.c` repertoire reference: `4caaa2aff00bb59d1155227c838ced106ab4d61717afcaa66328cb0b3136785b`

`licenses/` contains the applicable Montserrat, DejaVu Sans, Source Han Sans,
and FontAwesome license texts. The font generator verifies the pinned input
hashes, derives the CJK and existing private-use selections from the reference
glyph comments, and rejects output that differs from the old 14/16 glyph union.
It uses `lv_font_conv` 1.5.3:

```sh
python tools/generate_status_fonts.py
python tools/check_font_coverage.py
```

The coverage audit checks the 14 px and 16 px chains, exact old-repertoire
parity at 20 px and 22 px, and required ASCII, Latin, punctuation/symbol, and
`東京が日本語你好世界` samples. It also checks that the 80 px clock has only
digits, colon, and hyphen. The audit checks glyph presence, not shaping or
physical legibility.
