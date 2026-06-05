"""Named style presets for image transformations.

Each preset maps a short key to a rich instruction that is prepended/merged
with whatever the user asks for. Presets are intentionally descriptive so the
Gemini image model has enough guidance to produce a strong result.
"""

PRESETS: dict[str, dict[str, str]] = {
    "nyc_vibe": {
        "label": "NYC Vibe + Bridges",
        "prompt": (
            "Reimagine this image with a vibrant New York City vibe: a bustling "
            "skyline with skyscrapers, warm golden-hour light, energetic street "
            "atmosphere, and iconic suspension bridges connecting people together "
            "across the scene. Keep the main subject recognizable while weaving in "
            "the bridges as a visual metaphor for people connecting."
        ),
    },
    "japanese_culture": {
        "label": "Japanese Culture",
        "prompt": (
            "Reimagine this image infused with traditional Japanese culture: cherry "
            "blossoms (sakura), torii gates, paper lanterns, delicate ukiyo-e "
            "woodblock textures, and a serene, harmonious composition. Preserve the "
            "main subject while surrounding it with this cultural aesthetic."
        ),
    },
    "bridges": {
        "label": "Connect People with Bridges",
        "prompt": (
            "Transform this image to emphasize human connection: add elegant bridges "
            "linking people across the scene, with warm lighting and a hopeful, "
            "uplifting mood that celebrates community and togetherness."
        ),
    },
    "cyberpunk": {
        "label": "Cyberpunk",
        "prompt": (
            "Transform this image into a neon-soaked cyberpunk scene with glowing "
            "magenta and cyan lights, rain-slicked streets, holographic signage, and "
            "a futuristic high-tech atmosphere."
        ),
    },
    "watercolor": {
        "label": "Watercolor",
        "prompt": (
            "Repaint this image as a soft, delicate watercolor artwork with gentle "
            "color bleeds, visible paper texture, and loose expressive brushwork."
        ),
    },
    "van_gogh": {
        "label": "Van Gogh",
        "prompt": (
            "Repaint this image in the style of Vincent van Gogh with thick swirling "
            "impasto brushstrokes, vivid expressive colors, and dynamic movement like "
            "Starry Night."
        ),
    },
    "ghibli": {
        "label": "Studio Ghibli",
        "prompt": (
            "Reimagine this image as a Studio Ghibli anime scene with soft painterly "
            "backgrounds, warm nostalgic lighting, lush nature, and a whimsical, "
            "heartfelt mood."
        ),
    },
    "pixar": {
        "label": "Pixar 3D",
        "prompt": (
            "Transform this image into a polished Pixar-style 3D animated render with "
            "expressive characters, soft global illumination, and vibrant, friendly "
            "colors."
        ),
    },
}


VIDEO_PRESETS: dict[str, dict[str, str]] = {
    "gentle_pan": {
        "label": "Gentle Camera Pan",
        "prompt": "A slow, smooth camera pan across the scene with subtle, natural motion.",
    },
    "dramatic_zoom": {
        "label": "Dramatic Zoom",
        "prompt": "A dramatic cinematic zoom-in toward the main subject with depth and energy.",
    },
    "subtle_motion": {
        "label": "Subtle Living Motion",
        "prompt": "Bring the scene to life with subtle motion: gentle breeze, soft light shifts, small movements.",
    },
    "magical_sparkles": {
        "label": "Magical Sparkles",
        "prompt": "Add magical floating sparkles and a soft glow that drift gracefully across the scene.",
    },
    "rain_mood": {
        "label": "Rain & Moody Lighting",
        "prompt": "Add falling rain, reflective wet surfaces, and moody atmospheric lighting.",
    },
    "drone_flyover": {
        "label": "Drone Flyover",
        "prompt": "A sweeping aerial drone flyover revealing the scene from above with smooth motion.",
    },
}


def get_preset_prompt(key: str) -> str | None:
    preset = PRESETS.get(key)
    return preset["prompt"] if preset else None


def get_video_preset_prompt(key: str) -> str | None:
    preset = VIDEO_PRESETS.get(key)
    return preset["prompt"] if preset else None


def list_presets() -> list[dict[str, str]]:
    return [{"key": key, "label": value["label"]} for key, value in PRESETS.items()]


def list_video_presets() -> list[dict[str, str]]:
    return [{"key": key, "label": value["label"]} for key, value in VIDEO_PRESETS.items()]
