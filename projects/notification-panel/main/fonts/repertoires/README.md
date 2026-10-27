# Notification text profile

The profile defines required encoded characters independently of the font
assets. Regular 16/20/22 px notification fonts contain its union plus the
existing glyphs; body styles obtain the same effective coverage through their
regular fallback. This is character coverage, not Google Fonts feature
conformance or a promise of OpenType/complex-script shaping.

| Pinned input | Characters |
| --- | ---: |
| Google Fonts Latin Core | 319 |
| Google Fonts Latin Plus | 110 |
| CLDR 49 English punctuation / numbers | 32 / 18 |
| CLDR 49 Simplified Chinese punctuation / numbers | 55 / 28 |
| 通用规范汉字表 2013 level 1 | 3,500 |
| Unicode Box Drawing U+2500–U+257F | 128 |
| Unicode Block Elements U+2580–U+259F | 32 |
| Unicode Braille Patterns U+2800–U+28FF | 256 |

These sets overlap. Their required union is **4,365 codepoints**. Preserving
the existing 4,972-glyph repertoire yields **5,327 glyphs** in each regular
font. Chinese coverage retains level 1; level 2, including `帧` and `阈`, is
deferred. Emoji, additional scripts and Nerd Font icons are outside the
declared profile. Existing legacy extras remain available.

## Google Fonts

`GF_Latin_Core.nam` and `GF_Latin_Plus.nam` are copied from
[`googlefonts/glyphsets`](https://github.com/googlefonts/glyphsets/tree/0ac0e515473e39247683ed473fd74728671e2364/data/results/nam)
at commit `0ac0e515473e39247683ed473fd74728671e2364`. Only encoded `.nam`
entries define this profile, not unencoded alternates or OpenType features.
The files are covered by [Apache License 2.0](../licenses/GoogleFonts-Glyphsets-LICENSE.txt).

| File | SHA-256 |
| --- | --- |
| `GF_Latin_Core.nam` | `26936a4812ff33ee62a2e8156b9976173755909be84138ca4794a33f123b7d18` |
| `GF_Latin_Plus.nam` | `9fc716a8ee03bcf2720ce77eceb4c8e193474eef09e563a1a5d770bb45678d56` |

## CLDR locale exemplars

The four `cldr-49-*.txt` files contain sorted `U+` values extracted from the
`characters/exemplarCharacters` paths whose types are `numbers` and
`punctuation`. They are not CLDR's main or auxiliary language exemplars.
Those language exemplars would not replace the Chinese character table.

Source: [CLDR release 49](https://github.com/unicode-org/cldr/tree/96e54b273d2da4ec6fbad350ba19ff23fa24a588/common),
commit `96e54b273d2da4ec6fbad350ba19ff23fa24a588`, under
[Unicode License V3](../licenses/Unicode-LICENSE.txt).
Resolve each path through locale inheritance, replacing a missing value or
`↑↑↑` with its parent value; do not union parent sets. English resolves from
`en.xml`. `zh_Hans.xml` contains identity only, so `zh-Hans → zh → root`
resolves both selected paths from `zh.xml`; no supplemental parent-locale
exception applies to this chain.

The pinned source hashes are:

| Path under `common/` | SHA-256 |
| --- | --- |
| `main/en.xml` | `f8cbf26964d0e349fc96d9e5a56292edcbaab25c8e64177ca4f111773dfb79cf` |
| `main/zh_Hans.xml` | `5e84f410a0d715051dddcf7fa3ac6bf346e9edbf3b885d6f5e86b807b63bede2` |
| `main/zh.xml` | `95a7bf986c594389498b7c9243e255d3c466ab27df7efed8be11e9a1fccfa6ec` |
| `main/root.xml` | `7447cc014ec93a1425a2ab51e3d2b5cc48e6865c95350caa0fca36cbebcc93e6` |
| `supplemental/supplementalData.xml` | `745150ec1408d50e1a272b65654a5dd645f3e60f5cc2898b576f725b28538d1a` |

Resolved lists and their hashes are checked by
[`status_font_repertoire.py`](../../../tools/status_font_repertoire.py).
Updating the profile requires an intentional source/version/hash review;
normal font generation and auditing consume these local lists offline.

To reproduce the resolved files from the pinned CLDR checkout (not as part of
a normal build), run from the notification-panel project:

```sh
python3 tools/status_font_repertoire.py --extract-cldr /path/to/cldr/common
```

The extractor verifies the source XML hashes and locale parent paths, then
checks every resolved output hash before updating the local lists. Tests also
cover missing values, explicit inheritance and unsupported set syntax.

## Source assignment and preservation

[`status-text-source-assignments.json`](status-text-source-assignments.json)
assigns all 355 additions to pinned font assets without overlap. Existing
glyphs remain on their original sources. Missing or unexpected output is an
error rather than permission to shrink the profile. The two currency gaps use
matching Montserrat Medium for Bitcoin and Noto Sans Medium for lari; see the
[font source guide](../README.md).

## Text-presentation fallback

`emoji-variation-sequences-17.0.txt` is copied from
[Unicode 17.0](https://www.unicode.org/Public/17.0.0/ucd/emoji/emoji-variation-sequences.txt),
SHA-256 `bb3d09ef03f206012c7532dd52dc0a21c9efddba0135ea4cf0d9201b8b9bba7e`,
under [Unicode License V3](../licenses/Unicode-LICENSE.txt).
[`generate_text_presentation.py`](../../../tools/generate_text_presentation.py)
intersects registered FE0E/FE0F sequence bases with all three generated regular
fonts and writes the host's installed table. Only those pairs use the
monochrome base glyph, for example `⚠️ → ⚠`. This is text fallback, not emoji
sequence support. Other selectors, unsupported bases and malformed selector
runs remain intact. Source text used for Open identity is retained separately.
