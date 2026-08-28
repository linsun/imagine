"""Post-production MCP server (stdio): end credits.

The audience is the cast, so the film ends with their names receding into the
distance -- a Star Wars crawl.

Veo cannot render legible text, so this is composited locally with ffmpeg:
deterministic, offline, correct every time. Which is what you want in front of
a room.

  brew install ffmpeg
  cast.txt          one name per line, # comments ok
  CREDITS_STYLE     crawl (default) | scroll   -- scroll is the flat fallback
  CREDITS_MUSIC     optional audio file; defaults to looping the film's own audio
  CREDITS_SECONDS   default 12
"""

import base64
import math
import sys
import os
import random
import shutil
import subprocess
import tempfile

from fastmcp import FastMCP
from PIL import Image, ImageDraw, ImageFont

mcp = FastMCP("post")

# The cast list. Looked up in order, so a file simply called `cast` works.
CAST_CANDIDATES = [os.environ.get("CAST_FILE", ""), "./cast", "./cast.txt"]
CREDITS_STYLE = os.environ.get("CREDITS_STYLE", "crawl").lower()
# The one heading above the cast. Not a film title -- the Director used to
# invent one ("The Audience") and it read as filler.
CREDITS_TITLE = os.environ.get("CREDITS_TITLE", "AGNTCon + MCPCon Japan")
# Music is OFF by default -- a drone under the credits is worse than silence.
#   "none"/blank -> no music (just the spoken thank-you)
#   a path       -> your own file  <-- do this for the real talk
#   "synth"      -> the soft pad
#   "film"       -> loop the film's own audio
CREDITS_MUSIC = os.environ.get("CREDITS_MUSIC", "none")
# Spoken over the start of the credits. Set empty to disable.
CREDITS_VOICE = os.environ.get(
    "CREDITS_VOICE", "Thank you for being part of the cast!")
CREDITS_VOICE_AT = float(os.environ.get("CREDITS_VOICE_AT", "0.3"))
# The last thing on screen: a QR to wherever the film lives. Blank = derive it
# from GITHUB_REPO + GITHUB_RELEASE_TAG.
# A short greeting before the film. One beat by default: Japanese only.
# Set INTRO_SECONDS=0 to skip it entirely. Change the words for the next
# conference.
INTRO_JA = os.environ.get("INTRO_JA", "ようこそ")
# Shown instead of INTRO_JA if no Japanese-capable font is installed. Tofu boxes
# on a projector are worse than romaji -- the SPOKEN line is the fun part and it
# plays either way.
INTRO_JA_FALLBACK = os.environ.get("INTRO_JA_FALLBACK", "Yokoso")
# The English beat is OFF: an English greeting on top of the Japanese one read
# as one beat too many on screen. Set INTRO_EN to any text to bring it back --
# empty beats are skipped, so nothing else needs changing.
INTRO_EN = os.environ.get("INTRO_EN", "")
# macOS `say` words-per-minute. Default is ~175, which sounds like an
# announcement. 215 with a lifted pitch sounds like someone pleased to see you.
INTRO_EN_RATE = os.environ.get("INTRO_EN_RATE", "215")
INTRO_JA_RATE = os.environ.get("INTRO_JA_RATE", "180")
# `say` inline pitch: [[pbas n]] where 50 is the voice's baseline.
INTRO_EN_PITCH = os.environ.get("INTRO_EN_PITCH", "62")
INTRO_SECONDS = float(os.environ.get("INTRO_SECONDS", "2.4"))   # per beat

QR_URL = os.environ.get("QR_URL", "")
# A pre-rendered QR, committed to the repo. Using it means the render path
# needs no `qrcode` package at all -- one less thing that can be missing on the
# day. Delete the file (or set QR_IMAGE elsewhere) to go back to generating it.
QR_IMAGE = os.environ.get("QR_IMAGE", "./assets/qr.png")
QR_SECONDS = float(os.environ.get("QR_SECONDS", "6"))
QR_CAPTION = os.environ.get("QR_CAPTION", "Take your film home")
# Veo clips run hot. <1 slows the film down (0.85 = 15% slower); 1.0 leaves it.
# Audio is time-stretched to match, so the music does not drift.
FILM_SPEED = float(os.environ.get("FILM_SPEED", "0.85"))
# 0 = derive the length from the cast size so the scroll speed stays readable
# no matter how many names there are.
CREDITS_SECONDS = float(os.environ.get("CREDITS_SECONDS", "0"))
CREDITS_MAX_SECONDS = float(os.environ.get("CREDITS_MAX_SECONDS", "14"))
SECONDS_PER_ROW = float(os.environ.get("CREDITS_SECONDS_PER_ROW", "0.5"))
VOICE_EN = os.environ.get("VOICE_EN", "Samantha")
VOICE_JA = os.environ.get("VOICE_JA", "Kyoko")
CRAWL_COLOR = (255, 200, 60)          # the yellow

_FONTS = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


# The subset of _FONTS that actually carries Japanese glyphs.
_CJK_FONTS = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/Hiragino Sans W4.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def _has_cjk(text: str) -> bool:
    return any(
        "\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff"
        or "\uff00" <= c <= "\uffef"
        for c in text
    )


def _has_cjk(text: str) -> bool:
    return any(
        "\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff"
        or "\uff00" <= c <= "\uffef"
        for c in text
    )


def _load(paths, size: int):
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    return None


def _cjk_font_available() -> bool:
    """Only true if a CJK font can actually be LOADED, not merely found.

    The earlier version checked existence only, so it reported success for a
    font `_font()` would never choose -- and the guard stayed silent while the
    screen filled with tofu boxes.
    """
    return _load(_CJK_FONTS, 24) is not None


def _font(size: int, text: str = "") -> ImageFont.FreeTypeFont:
    """A font that can render `text`.

    When the text contains Japanese, CJK-capable faces are tried FIRST --
    otherwise a Latin font wins the race and every glyph renders as a box.
    This applies to cast names too, not just the greeting.
    """
    if text and _has_cjk(text):
        font = _load(_CJK_FONTS, size)
        if font is not None:
            return font
    return _load(_FONTS, size) or ImageFont.load_default()


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found. Install it: brew install ffmpeg")
    return exe


def _probe(path: str) -> dict:
    p = shutil.which("ffprobe") or "ffprobe"
    out = subprocess.run(
        [p, "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    info = {"width": 1280, "height": 720, "fps": 24, "has_audio": False}
    for line in out:
        parts = [x for x in line.split(",") if x]
        if parts and parts[0] == "audio":
            info["has_audio"] = True
        elif parts and parts[0] == "video" and len(parts) >= 4:
            info["width"], info["height"] = int(parts[1]), int(parts[2])
            num, _, den = parts[3].partition("/")
            try:
                info["fps"] = max(1, round(float(num) / float(den or 1)))
            except ZeroDivisionError:
                pass
    return info


def _audio_seconds(path: str) -> float:
    p = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([p, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _voice(ff: str, text: str, path: str, voice: str = "",
           rate: str = "", pitch: str = "") -> bool:
    """Render a line with the system voice. macOS `say`, else espeak.

    `rate` (words per minute) and `pitch` are what make a greeting sound
    excited rather than announced.
    """
    if not text.strip():
        return False
    raw = path + ".src"
    if shutil.which("say"):
        name = voice or VOICE_EN
        # Fall back to the default voice if the requested one is not installed
        # -- a missing Japanese voice should cost the accent, not the line.
        installed = subprocess.run(["say", "-v", "?"], capture_output=True,
                                   text=True).stdout
        if name not in installed:
            name = VOICE_EN
        spoken = f"[[pbas {pitch}]]{text}" if pitch else text
        cmd = ["say", "-v", name]
        if rate:
            cmd += ["-r", str(rate)]
        cmd += ["-o", raw + ".aiff", spoken]
        r = subprocess.run(cmd, capture_output=True)
        src = raw + ".aiff"
    elif shutil.which("espeak-ng") or shutil.which("espeak"):
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        r = subprocess.run([exe, "-w", raw + ".wav", text], capture_output=True)
        src = raw + ".wav"
    else:
        return False
    if r.returncode != 0 or not os.path.exists(src):
        return False
    # Normalise to a consistent, clearly audible level. System voices vary a
    # lot and a quiet one under a starfield is easy to miss.
    r2 = subprocess.run([ff, "-y", "-i", src, "-af", "loudnorm=I=-14:TP=-1.5",
                         "-ac", "2", "-ar", "48000", path], capture_output=True)
    if r2.returncode != 0 or not os.path.exists(path):
        return False
    return _audio_seconds(path) > 0.3


def _synth_pad(ff: str, seconds: float, path: str) -> bool:
    """A soft, quiet chord pad. Deliberately understated -- it plays under the
    cast list, it is not the point. Supply CREDITS_MUSIC for the real talk."""
    chord = [174.61, 220.00, 261.63, 329.63]          # F major-ish, warm
    ins, labels = [], []
    for i, f in enumerate(chord):
        ins += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration={seconds + 1}"]
        labels.append(f"[{i}:a]")
    graph = (
        "".join(labels) + f"amix=inputs={len(chord)}:normalize=1[mix];"
        "[mix]tremolo=f=0.25:d=0.35,lowpass=f=1400,"
        # 3.5 + limiter measures ~-20 dB mean, which matches typical Veo audio,
        # so the cut from film to credits is not a jump in either direction.
        f"volume=3.5,alimiter=limit=0.7,"
        f"afade=t=in:st=0:d=2,afade=t=out:st={max(0.1, seconds - 2.5)}:d=2.5[a]"
    )
    r = subprocess.run([ff, "-y", *ins, "-filter_complex", graph,
                        "-map", "[a]", "-ac", "2", "-ar", "48000",
                        "-t", str(seconds), path], capture_output=True)
    return r.returncode == 0 and os.path.exists(path)


def _cast_path() -> str:
    for c in CAST_CANDIDATES:
        if c and os.path.exists(c):
            return c
    return ""


def _default_url() -> str:
    """The caption under the QR. The image itself is ./assets/qr.png."""
    repo = os.environ.get("GITHUB_REPO", "").strip()
    tag = os.environ.get("GITHUB_RELEASE_TAG", "").strip()
    if repo and tag:
        return f"https://github.com/{repo}/releases/tag/{tag}"
    # Fall back to whatever the committed QR encodes, so the card still renders.
    return "https://github.com/linsun/imagine/releases/tag/agntcon-mcpcon-japan-2026"


def _read_cast() -> list[str]:
    path = _cast_path()
    if not path:
        return []
    return [ln.strip() for ln in open(path, encoding="utf-8")
            if ln.strip() and not ln.strip().startswith("#")]


def _layout(n: int) -> int:
    """Columns. A single column of 139 names would crawl for over two minutes,
    or blur past unread. Columns trade width for time."""
    if n <= 14:
        return 1
    if n <= 34:
        return 2
    if n <= 70:
        return 3
    if n <= 110:
        return 4
    # Beyond this, more columns is actually MORE readable at a fixed duration:
    # a shorter card scrolls slower, so each name is on screen longer.
    return 5


def _duration(n: int) -> float:
    """Length derived from the cast size, so the SPEED is constant.

    With a fixed duration, more names simply means faster scrolling, which is
    why most of the cast flew past unread.
    """
    if CREDITS_SECONDS > 0:
        return CREDITS_SECONDS
    rows = math.ceil(n / _layout(n))
    return max(8.0, min(CREDITS_MAX_SECONDS, 3.0 + rows * SECONDS_PER_ROW))


def _card(width: int, height: int, title: str, subtitle: str,
          names: list[str], path: str) -> tuple[str, int]:
    """The tall card that scrolls past. Returns (path, columns).

    `title`/`subtitle` from the caller are ignored: the heading is always
    CREDITS_TITLE, so a model cannot put words on your screen.
    """
    title, subtitle = CREDITS_TITLE, ""
    cols = _layout(len(names))
    rows = math.ceil(len(names) / cols)

    title_size = max(30, width // 18)
    name_size = max(20, int(width / (16 + 10 * cols)))
    small = max(16, width // 44)
    line_h = int(name_size * 2.1)

    # Essentially no lead-in: the card starts at the frame edge so the first names
    # are moving from the first frame. Any gap here is dead starfield while the
    # thank-you plays -- 0.18 of a frame height cost 1.5 seconds of nothing.
    top_gap = int(height * 0.02)
    head = int(title_size * 1.7) + (int(small * 2.6) if subtitle else 0) + int(small * 2.8)
    # Barely any tail: the last name should clear the frame as the segment
    # ends, so the QR follows immediately instead of after seconds of stars.
    total = top_gap + head + rows * line_h + int(height * 0.06)

    img = Image.new("RGB", (width, total), (0, 0, 0))
    d = ImageDraw.Draw(img)
    f_t, f_n, f_s = _font(title_size), _font(name_size), _font(small)
    col_w = width / cols
    max_w = int(col_w * 0.90)

    def wrap(text: str, font):
        """Fit a name into its column: wrap onto a second line if needed, and
        only then shrink. Shrinking alone bottoms out and long names collide
        with their neighbours -- which is exactly what happened with
        'National Institute of Advanced Industrial Science and Technology'."""
        if d.textbbox((0, 0), text, font=font)[2] <= max_w:
            return [text], font
        words = text.split()
        if len(words) > 1:
            best = None
            for i in range(1, len(words)):
                a, b = " ".join(words[:i]), " ".join(words[i:])
                w = max(d.textbbox((0, 0), a, font=font)[2],
                        d.textbbox((0, 0), b, font=font)[2])
                if best is None or w < best[0]:
                    best = (w, [a, b])
            if best and best[0] <= max_w:
                return best[1], font
            lines = best[1] if best else [text]
        else:
            lines = [text]
        size = font.size
        while size > 11:
            f2 = _font(size, " ".join(lines))
            if max(d.textbbox((0, 0), ln, font=f2)[2] for ln in lines) <= max_w:
                return lines, f2
            size = int(size * 0.92)
        return lines, _font(11)

    wrapped = [wrap(n, f_n) for n in names]
    if any(len(w[0]) > 1 for w in wrapped):
        line_h = int(name_size * 3.0)      # room for two-line entries

    total = top_gap + head + rows * line_h + int(height * 0.06)
    img = Image.new("RGB", (width, total), (0, 0, 0))
    d = ImageDraw.Draw(img)

    def centre(text, font, y, cx=None):
        cx = width // 2 if cx is None else cx
        w = d.textbbox((0, 0), text, font=font)[2]
        d.text((cx - w // 2, y), text, font=font, fill=CRAWL_COLOR)

    y = top_gap
    if title:
        centre(title.upper(), f_t, y); y += int(title_size * 1.7)
    if subtitle:
        centre(subtitle, f_s, y); y += int(small * 2.6)
    centre("THE CAST", f_s, y); y += int(small * 2.8)

    for i, (lines, font) in enumerate(wrapped):
        r, c = divmod(i, cols)
        cx = int(col_w * (c + 0.5))
        y0 = y + r * line_h
        for j, ln in enumerate(lines):
            centre(ln, font, y0 + j * int(font.size * 1.25), cx=cx)
    img.save(path)
    return path, cols


def _qr_card(width: int, height: int, url: str, path: str) -> bool:
    """A QR big enough to scan from the back of a room, on white for contrast."""
    box = int(height * 0.72)
    code = None
    if QR_IMAGE and os.path.exists(QR_IMAGE):
        try:
            code = Image.open(QR_IMAGE).convert("RGB").resize((box, box), Image.NEAREST)
        except Exception as exc:  # noqa: BLE001
            print(f"post-mcp: could not read {QR_IMAGE}: {exc}", file=sys.stderr, flush=True)
    if code is None:
        try:
            import qrcode
        except ImportError:
            print("post-mcp: no QR -- no pre-rendered ./assets/qr.png and the "
                  "`qrcode` package is not installed. Run `make install`.",
                  file=sys.stderr, flush=True)
            return False
        qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        code = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        code = code.resize((box, box), Image.NEAREST)

    img = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(img)
    # White quiet zone around it -- QRs on black are unreliable to scan.
    pad = int(box * 0.06)
    plate = Image.new("RGB", (box + pad * 2, box + pad * 2), (255, 255, 255))
    plate.paste(code, (pad, pad))
    img.paste(plate, ((width - plate.width) // 2, (height - plate.height) // 2))

    # No caption, no URL: the presenter says it out loud, and a bare code is a
    # stronger closing frame than a code with instructions under it.
    img.save(path)
    return True


def _title_card(width: int, height: int, text: str, path: str,
                background: str = "") -> None:
    """One big line, centred. Same starfield and yellow as the credits, so the
    opening and the closing read as the same film rather than two idents."""
    if background and os.path.exists(background):
        img = Image.open(background).convert("RGB").resize((width, height))
    else:
        img = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(img)
    size = max(36, width // 14)
    font = _font(size, text)
    while d.textbbox((0, 0), text, font=font)[2] > width * 0.86 and size > 18:
        size = int(size * 0.92)
        font = _font(size, text)
    box = d.textbbox((0, 0), text, font=font)
    d.text(((width - box[2]) // 2, (height - box[3]) // 2), text,
           font=font, fill=CRAWL_COLOR)
    img.save(path)


def _intro_voices(ff: str, tmp: str) -> list[tuple]:
    """Render the spoken beats first, so their length decides the intro's.

    The music has to cover intro + credits + QR as one continuous take, and we
    cannot cut that until we know how long the greeting runs.
    """
    beats = [
        (INTRO_JA, VOICE_JA, INTRO_JA_RATE, ""),
        (INTRO_EN, VOICE_EN, INTRO_EN_RATE, INTRO_EN_PITCH),
    ]
    out = []
    for i, (text, voice, rate, pitch) in enumerate(beats):
        if not text.strip():
            continue
        wav = os.path.join(tmp, f"introv{i}.wav")
        spoken = _voice(ff, text, wav, voice, rate=rate, pitch=pitch)
        dur = max(INTRO_SECONDS, _audio_seconds(wav) + 1.0) if spoken else INTRO_SECONDS
        out.append((i, text, wav if spoken else "", dur))
    return out


def _intro(ff: str, w: int, h: int, fps: int, tmp: str, voices: list,
           stars: str = "", music: str = "") -> str:
    """The greeting beats, each spoken aloud. Japanese only by default.

    Built as its own segment and prepended, so it plays before the film with no
    dependence on what Veo decided to generate.
    """
    segs = []
    at = 0.0
    for i, text, wav, dur in voices:
        # Speak the real Japanese regardless; only the on-screen text falls back.
        shown = text
        if _has_cjk(text) and not _cjk_font_available():
            shown = INTRO_JA_FALLBACK
            print("post-mcp: no Japanese font installed, showing "
                  f"{shown!r} on screen (the spoken line is unchanged).",
                  file=sys.stderr, flush=True)
        png = os.path.join(tmp, f"intro{i}.png")
        seg = os.path.join(tmp, f"intro{i}.mp4")
        _title_card(w, h, shown, png, background=stars)

        # Voice over the music, both from the same continuous track the credits
        # will keep playing later.
        ins, parts, idx = [], [], 1
        if music and os.path.exists(music):
            ins += ["-ss", str(at), "-t", str(dur), "-i", music]
            parts.append(f"[{idx}:a]volume=1.0[m]")
            midx = idx
            idx += 1
        else:
            midx = None
        if wav:
            ins += ["-i", wav]
            parts.append(f"[{idx}:a]adelay=400|400,volume=1.0[vv]")
            vidx = idx
            idx += 1
        else:
            vidx = None

        if midx is None and vidx is None:
            ins += ["-f", "lavfi", "-t", str(dur), "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000"]
            afilter, amap = "", "1:a"
        else:
            srcs = ("[m]" if midx is not None else "") + ("[vv]" if vidx is not None else "")
            n = int(midx is not None) + int(vidx is not None)
            if n == 1:
                parts.append(f"{srcs}apad,atrim=0:{dur}[a]")
            else:
                parts.append(f"{srcs}amix=inputs=2:normalize=0:duration=longest,"
                             f"alimiter=limit=0.9,apad,atrim=0:{dur}[a]")
            afilter, amap = ";" + ";".join(parts), "[a]"

        subprocess.run([
            ff, "-y", "-loop", "1", "-t", str(dur), "-i", png, *ins,
            "-filter_complex",
            f"[0:v]scale={w}:{h},setsar=1,fps={fps},format=yuv420p[v]" + afilter,
            "-map", "[v]", "-map", amap, "-t", str(dur),
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "192k", seg], check=True, capture_output=True)
        segs.append(seg)
        at += dur
    if not segs:
        return ""
    if len(segs) == 1:
        return segs[0]
    out = os.path.join(tmp, "intro.mp4")
    subprocess.run([
        ff, "-y", "-i", segs[0], "-i", segs[1], "-filter_complex",
        f"[0:v]scale={w}:{h},setsar=1,fps={fps}[a0];"
        f"[1:v]scale={w}:{h},setsar=1,fps={fps}[a1];"
        "[a0][0:a][a1][1:a]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", out],
        check=True, capture_output=True)
    return out


def _starfield(width: int, height: int, path: str) -> None:
    img = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(img)
    rnd = random.Random(7)
    for _ in range(int(width * height / 3000)):
        x, y = rnd.randrange(width), rnd.randrange(height)
        v = rnd.randrange(90, 255)
        r = 1 if v < 210 else 2
        d.ellipse([x - r, y - r, x + r, y + r], fill=(v, v, v))
    img.save(path)


def _fade_top(width: int, height: int, path: str) -> None:
    """Black-to-clear gradient so the text vanishes into the distance."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    span = int(height * 0.30)
    for y in range(span):
        a = int(235 * (1 - y / span) ** 1.6)
        d.line([(0, y), (width, y)], fill=(0, 0, 0, a))
    img.save(path)


@mcp.tool
def add_credits(video_b64: str, title: str = "", subtitle: str = "",
                names: list[str] | None = None, seconds: float = 0,
                style: str = "") -> dict:
    """Append end credits listing the cast, and return the finished film.

    Args:
        video_b64: The film.
        title: The film's title, shown at the top of the crawl.
        subtitle: Smaller line under it.
        names: Omit -- the cast comes from cast.txt.
        seconds: Crawl duration. Omit for the default.
        style: "crawl" (Star Wars perspective) or "scroll" (flat). Omit for default.

    Returns: { video_b64, mime, names_count, seconds, style }
    """
    ff = _ffmpeg()
    # The FILE always wins. The model does not get to retype 139 company names.
    cast = _read_cast() or (names or [])
    if not cast:
        raise RuntimeError(
            "No cast list found. Looked for: " +
            ", ".join(c for c in CAST_CANDIDATES if c))
    dur = seconds or _duration(len(cast))
    mode = (style or CREDITS_STYLE).lower()
    # The scroll is timed over `dur`, but we cut the segment slightly early:
    # the last name is still leaving the top when the QR appears, so there is
    # no gap of empty starfield between them.
    roll_dur = max(1.0, dur - 0.9)

    tmp = tempfile.mkdtemp(prefix="credits-")
    film = os.path.join(tmp, "film.mp4")
    card = os.path.join(tmp, "card.png")
    stars = os.path.join(tmp, "stars.png")
    fade = os.path.join(tmp, "fade.png")
    music = os.path.join(tmp, "music.wav")
    voice = os.path.join(tmp, "voice.wav")
    roll = os.path.join(tmp, "roll.mp4")
    out = os.path.join(tmp, "out.mp4")

    with open(film, "wb") as f:
        f.write(base64.b64decode(video_b64))
    info = _probe(film)
    w, h, fps = info["width"], info["height"], info["fps"]

    _, cols = _card(w, h, title, subtitle, cast, card)
    _starfield(w, h, stars)
    _fade_top(w, h, fade)

    # The greeting is voiced FIRST, because its length decides how much music we
    # need. One track then runs across intro, credits and QR as a single take --
    # the film sits in the middle with its own audio, and the music picks up
    # afterwards exactly where it would have been.
    intro_voices = _intro_voices(ff, tmp) if INTRO_SECONDS > 0 else []
    intro_dur = sum(v[3] for v in intro_voices)

    have_music = False
    choice = (CREDITS_MUSIC or "none").strip().lower()
    total_audio = intro_dur + roll_dur + (QR_SECONDS if QR_SECONDS > 0 else 0)
    if choice in ("none", ""):
        pass
    elif choice == "film" and info["has_audio"]:
        subprocess.run([ff, "-y", "-i", film, "-vn", "-ac", "2", "-ar", "48000",
                        "-af", "volume=0.4", music], capture_output=True, check=False)
        have_music = os.path.exists(music)
    elif choice == "synth":
        have_music = _synth_pad(ff, total_audio, music)
    elif os.path.exists(CREDITS_MUSIC):
        subprocess.run([ff, "-y", "-stream_loop", "-1", "-i", CREDITS_MUSIC,
                        "-t", str(total_audio), "-ac", "2", "-ar", "48000", "-af",
                        # loudnorm, not a blind gain: any track lands just under
                        # the spoken line instead of depending on how hot it was.
                        f"loudnorm=I=-20:TP=-2,"
                        f"afade=t=out:st={max(0.1, total_audio - 3.0)}:d=3.0",
                        music], capture_output=True, check=False)
        have_music = os.path.exists(music)

    # Now the greeting: same starfield, same track, so it bookends the credits.
    if intro_voices:
        intro = _intro(ff, w, h, fps, tmp, intro_voices,
                       stars=stars, music=music if have_music else "")
        if intro:
            merged = os.path.join(tmp, "intro_film.mp4")
            subprocess.run([
                ff, "-y", "-i", intro, "-i", film, "-filter_complex",
                f"[0:v]scale={w}:{h},setsar=1,fps={fps}[i0];"
                f"[1:v]scale={w}:{h},setsar=1,fps={fps}[i1];"
                "[i0][0:a][i1][1:a]concat=n=2:v=1:a=1[v][a]",
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", merged],
                check=True, capture_output=True)
            film = merged
            info["has_audio"] = True

    have_voice = _voice(ff, CREDITS_VOICE, voice)

    if mode == "crawl":
        # Squeeze the top so the text recedes. The card is bright-on-black, so
        # it composites over the starfield with a `lighten` blend -- no alpha
        # channel has to survive the perspective warp, which is the fragile bit.
        x_l, x_r = int(w * 0.34), int(w * 0.66)
        vf = (f"[0:v][2:v]overlay=x=0:y='H-(H+h)*t/{dur}':shortest=1[scroll];"
              f"[scroll]perspective="
              f"x0={x_l}:y0=0:x1={x_r}:y1=0:x2=0:y2={h}:x3={w}:y3={h}"
              f":sense=destination:interpolation=linear[warp];"
              # blend must run in RGB: on YUV it maxes the chroma planes
              # independently and the yellow comes out pink.
              f"[warp]format=gbrp[warprgb];[1:v]format=gbrp[starsrgb];"
              f"[starsrgb][warprgb]blend=all_mode=lighten[lit];"
              f"[lit][3:v]overlay=0:0,format=yuv420p[v]")
        inputs = ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:d={dur}:r={fps}",
                  "-loop", "1", "-i", stars,
                  "-loop", "1", "-i", card,
                  "-loop", "1", "-i", fade]
    else:
        vf = (f"[0:v][1:v]overlay=x=0:y='H-(H+h)*t/{dur}':shortest=1,"
              f"format=yuv420p[v]")
        inputs = ["-f", "lavfi", "-i", f"color=c=0x08090b:s={w}x{h}:d={dur}:r={fps}",
                  "-loop", "1", "-i", card]

    # Audio is the input AFTER all the video inputs. Count -i flags; do not
    # infer it from list length, which counts flags too.
    n_video = inputs.count("-i")
    audio_in: list[str] = []
    parts: list[str] = []
    idx = n_video
    if have_music:
        # Continue the track from where the greeting left it.
        audio_in += ["-ss", str(intro_dur), "-i", music]
        parts.append(f"[{idx}:a]volume=1.0[m]")
        idx += 1
    if have_voice:
        audio_in += ["-i", voice]
        delay = int(CREDITS_VOICE_AT * 1000)
        parts.append(f"[{idx}:a]adelay={delay}|{delay},volume=1.0[vv]")
        idx += 1
    if not have_music and not have_voice:
        audio_in += ["-f", "lavfi", "-i",
                     "anullsrc=channel_layout=stereo:sample_rate=48000"]
        afilter, amap = "", f"{n_video}:a"
    else:
        srcs = ("[m]" if have_music else "") + ("[vv]" if have_voice else "")
        n_src = int(have_music) + int(have_voice)
        if n_src == 1:
            parts.append(f"{srcs}apad,atrim=0:{dur}[a]")
        else:
            parts.append(f"{srcs}amix=inputs=2:normalize=0:duration=longest,"
                         f"alimiter=limit=0.9,apad,atrim=0:{dur}[a]")
        afilter, amap = ";" + ";".join(parts), "[a]"

    subprocess.run([ff, "-y", *inputs, *audio_in,
                    "-filter_complex", vf + afilter,
                    "-map", "[v]", "-map", amap,
                    "-c:v", "libx264", "-preset", "veryfast", "-r", str(fps),
                    "-c:a", "aac", "-b:a", "192k", "-t", str(roll_dur), roll],
                   check=True, capture_output=True)

    sp = FILM_SPEED if 0.5 <= FILM_SPEED <= 2.0 else 1.0
    # setpts stretches the picture; atempo stretches the sound by the same
    # factor so Veo's own music stays in sync with it.
    vslow = "" if sp == 1.0 else f",setpts=PTS/{sp}"
    aslow = "" if sp == 1.0 else f"atempo={sp},"
    if info["has_audio"]:
        cmd = [ff, "-y", "-i", film, "-i", roll, "-filter_complex",
               f"[0:v]scale={w}:{h},setsar=1{vslow},fps={fps}[v0];"
               f"[1:v]scale={w}:{h},setsar=1,fps={fps}[v1];"
               f"[0:a]{aslow}aresample=48000[a0];"
               "[v0][a0][v1][1:a]concat=n=2:v=1:a=1[v][a]"]
    else:
        cmd = [ff, "-y", "-i", film, "-i", roll,
               "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
               "-filter_complex",
               f"[0:v]scale={w}:{h},setsar=1{vslow},fps={fps}[v0];"
               f"[1:v]scale={w}:{h},setsar=1,fps={fps}[v1];"
               "[v0][2:a][v1][1:a]concat=n=2:v=1:a=1[v][a]"]
    cmd += ["-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", out]
    subprocess.run(cmd, check=True, capture_output=True)

    # ---- final card: the QR, then the film ends -------------------------
    url = QR_URL or _default_url()
    qr_png = os.path.join(tmp, "qr.png")
    qr_seg = os.path.join(tmp, "qr.mp4")
    withqr = os.path.join(tmp, "withqr.mp4")
    qr_ok = bool(url) and QR_SECONDS > 0 and _qr_card(w, h, url, qr_png)
    if qr_ok:
        # Continue the same track from where the credits left off, so the cut
        # to the QR card is invisible in the audio.
        if have_music:
            qr_audio = ["-ss", str(intro_dur + roll_dur), "-t", str(QR_SECONDS),
                        "-i", music]
        else:
            qr_audio = ["-f", "lavfi", "-t", str(QR_SECONDS), "-i",
                        "anullsrc=channel_layout=stereo:sample_rate=48000"]
        subprocess.run([
            ff, "-y", "-loop", "1", "-t", str(QR_SECONDS), "-i", qr_png,
            *qr_audio,
            "-vf", f"scale={w}:{h},setsar=1,fps={fps},format=yuv420p",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k",
            qr_seg], check=True, capture_output=True)
        subprocess.run([
            ff, "-y", "-i", out, "-i", qr_seg, "-filter_complex",
            f"[0:v]scale={w}:{h},setsar=1,fps={fps}[a0];"
            f"[1:v]scale={w}:{h},setsar=1,fps={fps}[a1];"
            "[a0][0:a][a1][1:a]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", withqr],
            check=True, capture_output=True)
        out = withqr

    with open(out, "rb") as f:
        data = f.read()
    return {"video_b64": base64.b64encode(data).decode("ascii"), "mime": "video/mp4",
            "names_count": len(cast), "seconds": round(dur, 1), "style": mode,
            "columns": cols,
            "music": CREDITS_MUSIC if have_music else "none",
            "voice": CREDITS_VOICE if have_voice else "",
            "voice_seconds": round(_audio_seconds(voice), 2) if have_voice else 0,
            "voice_engine": ("say" if shutil.which("say")
                             else ("espeak" if (shutil.which("espeak-ng")
                                                or shutil.which("espeak")) else "NONE")),
            "cast_file": _cast_path(),
            "film_speed": sp,
            "qr": url if qr_ok else "",
            "qr_problem": "" if qr_ok else ("no url (set GITHUB_REPO + GITHUB_RELEASE_TAG)"
                                            if not url else "qrcode package missing -- make install"),
            "qr_seconds": QR_SECONDS if qr_ok else 0}


@mcp.tool
def list_cast() -> dict:
    """Who is currently in the credits."""
    cast = _read_cast()
    return {"file": CAST_FILE, "count": len(cast), "names": cast}


if __name__ == "__main__":
    mcp.run()
