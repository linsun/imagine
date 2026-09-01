# Where the words come from

## The shape

```
you type        "make them dance, japanese vibe"
   │
   ▼
Scout (A2A)     enriches ONLY your idea, 20 words
   │            "dancing joyfully, paper lanterns and drifting sakura, warm light"
   ▼
PRESERVE_IMAGE  a fixed clause added IN CODE, wrapped round the idea
   │            ↳ agent/director.py
   ▼
Nano Banana     the same room, the same faces, with your idea applied
   │
   ▼
DP (A2A)        motion + music, 25 words
   ▼
PRESERVE_VIDEO  the same fixed clause, again in code
   ▼
Veo             they move, the room does not
```

## The preservation clause is not negotiable

It lives in `agent/director.py` as `PRESERVE_IMAGE` / `PRESERVE_VIDEO` and is
wrapped around whatever is asked for **in code**, before the request leaves.
A model cannot forget it or write over it.

This is the fix for the scene being replaced: when the crew was free to write
the whole instruction, it produced a beautiful Kyoto shrine courtyard with
strangers in it. The wording is lifted from the presets that worked at KCD —
*"keep the main subject recognizable"*.

## It contains no location, and that is on purpose

There is nothing about Japan anywhere in the code. The theme comes only from
what you type, so the same agent works at the next conference with no changes:

```
you › make them dance, japanese vibe
you › make them dance with a brazilian carnival vibe
you › make everyone look like a 1920s jazz band
```

The Scout is explicitly forbidden from inventing a place you did not name, and
from describing the people or the venue at all.

## Using a photo instead of the camera

```
you › use the photo at ~/Desktop/room.jpg
you › what photos are in ~/Pictures/kcd
```

Any path works — `~`, relative, absolute. This is the sane way to test with a
proper picture, and the fallback if the camera fails on stage.

## Overriding it live

```
you › redo the still, but make the lanterns much brighter
you › make the film with exactly this shot: <your own line>
```

Your wording is used verbatim. The preservation clause is still applied.

## Tuning the DP (before the talk, not during)

`agent/crew.py`, the `DP` constant. Veo obeys concrete direction and ignores
vague direction:

| Say this | Not this |
| --- | --- |
| "slow 15° push in, locked tripod" | "nice camera movement" |
| "warm dusk key light, paper lanterns as practicals" | "good lighting" |
| "upbeat taiko and shamisen, crowd laughing and clapping" | "happy music" |
| "people stay recognisable, faces unchanged" | (nothing) |
| "no text, no captions, no watermarks" | (nothing) |

That last one matters — Veo likes to invent unreadable text, and your credits
are already handled properly by ffmpeg.

**Veo generates its own audio.** If you do not name the music you get whatever
it feels like, or silence. The DP prompt already forbids returning a shot with
no audio direction — keep that rule if you rewrite it.

## Tuning the Scout

Same file, the `SCOUT` constant. This is where cultural specificity lives.
"Japanese dance" is a wide net: Bon Odori at a summer matsuri reads completely
differently from a Nihon-buyō stage or a Yosakoi team. Pick one deliberately,
and check the wording with someone Japanese before the talk — that is the
difference between charming and clumsy in a Tokyo room.

## Presets — the safety net

`../mcp-server/vision_mcp/presets.py` still holds the original style and motion
presets. The Director doesn't reach for them (the Scout and DP replace them),
but they remain a deterministic fallback:

```
you › transform it with the japanese_culture preset
```

Worth keeping for the stage: a preset cannot have a bad day.

## Credits

Handled entirely by ffmpeg — no model involved, so it cannot have a bad day.
The **cast file always wins**; the Director cannot retype 139 company names
even if it tries.

| | |
| --- | --- |
| cast list | `$CAST_FILE`, else `./cast`, else `./cast.txt` |
| `CREDITS_SECONDS=0` | length derived from the cast size, so the *speed* is constant |
| `CREDITS_MAX_SECONDS` | ceiling, default 20s |
| `CREDITS_VOICE` | spoken at the start; empty to disable |
| `CREDITS_MUSIC` | `none` (default) · a path to your file · `synth` · `film` |
| `CREDITS_STYLE` | `crawl` or `scroll` |

Columns are automatic — 1/2/3/4/5 by cast size. Past ~110 names more columns
is actually *more* readable at a fixed length: a shorter card scrolls slower,
so each name stays on screen longer.
Long names wrap onto a second line before shrinking, because shrinking alone
bottoms out and neighbours collide.

139 names now fits in **20 seconds** across 5 columns, ~45 on screen at once.

If the thank-you is silent, `add_credits` reports why: check `voice_engine`
in its result. `NONE` means no speech binary was found in the process that
agentgateway spawned; `say` with `voice_seconds: 0` means macOS refused to
render it. Test directly with:

```
say -v Samantha "Thank you for being part of the cast!"
```

## Model knobs

`.env`:

| | |
| --- | --- |
| `VIDEO_DURATION` | 4, 6 or 8 seconds. Shorter generates faster. |
| `VIDEO_MODEL` | `./imagine models` shows what your key can use |
| `IMAGE_MODEL` | the gateway's name for it, e.g. `nano-banana` |
