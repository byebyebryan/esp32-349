# Noto Sans Medium fallback source

This pinned source supplies **only U+20BE GEORGIAN LARI SIGN** in the regular
notification fonts. Its converter output fits the existing vertical bounds at
16/20/22 px. It does not replace other glyphs or extend the declared scripts.

The source comes from official
[`google/fonts`](https://github.com/google/fonts/tree/ddf386ba1b9fbc6b2c818f1ddcd9148f54ced94b/ofl/notosans)
commit `ddf386ba1b9fbc6b2c818f1ddcd9148f54ced94b`, file
`ofl/notosans/NotoSans[wdth,wght].ttf`. The source is Noto Sans v2.015,
licensed under the adjacent [SIL Open Font License 1.1](OFL.txt).

| Input/output | SHA-256 |
| --- | --- |
| Original variable TTF | `bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d` |
| Checked-in `NotoSans-Medium.ttf` | `30202917c4a1f13275fc778e23320f6b3050e88c0e49c98dd3a0f8fdb7e4099e` |
| `OFL.txt` | `cee9892f9f0cc8fe882c9e9537ee6a89621d86ee7ceaf70b02e2b2b1c25c061a` |

The checked-in file is a weight-500 instance. It retains the width axis
(62.5–100); conversion uses its normal-width default of 100. It is not a fully
static font. Font names are inherited from the upstream source.

To reproduce the asset, verify the original source hash, then run these
authoring commands in this directory, substituting the downloaded source path:

```sh
uvx --from fonttools==4.68.0 fonttools varLib.instancer \
  /path/to/NotoSans-variable.ttf wght=500 -o NotoSans-Medium.ttf
uv run --no-project --with fonttools==4.68.0 python - <<'PY'
from fontTools.ttLib import TTFont
font = TTFont("NotoSans-Medium.ttf", recalcTimestamp=False)
font["head"].modified = 3875972411
font.save("NotoSans-Medium.ttf")
PY
```

The fixed modified timestamp is 2026-10-27 19:00:11 UTC, retained for reproducible
asset bytes rather than using the authoring machine's current time. Verify the
resulting full-file hash above. FontTools is needed only to recreate this
vendored asset; ordinary offline generation/audits use the checked-in TTF and
do not require it.
