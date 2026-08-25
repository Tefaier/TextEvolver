from io import BytesIO
from math import ceil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from text_evolver.processing.binary_converter import convert_binary

CAPTION_PADDING = 6
CAPTION_SPACING = 4


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bounds = draw.textbbox((0, 0), text, font=font)
    return ceil(bounds[2] - bounds[0])


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    """Wrap at word boundaries using rendered pixel width, preserving explicit newlines."""
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue

        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if _text_width(draw, candidate, font) <= max_width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return "\n".join(lines)


def _caption_image(text: str, font: ImageFont.ImageFont, max_width: int) -> Image.Image:
    wrapped_text = _wrap_text(text, font, max(1, max_width - (CAPTION_PADDING * 2)))
    measuring_draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    bounds = measuring_draw.multiline_textbbox(
        (0, 0),
        wrapped_text,
        font=font,
        spacing=CAPTION_SPACING,
        align="center",
    )
    text_width = ceil(bounds[2] - bounds[0])
    text_height = ceil(bounds[3] - bounds[1])
    label = Image.new(
        "RGB",
        (text_width + (CAPTION_PADDING * 2), text_height + (CAPTION_PADDING * 2)),
        "white",
    )
    ImageDraw.Draw(label).multiline_text(
        (CAPTION_PADDING - bounds[0], CAPTION_PADDING - bounds[1]),
        wrapped_text,
        fill="black",
        font=font,
        spacing=CAPTION_SPACING,
        align="center",
    )
    return label


def get_image(binary: object, name: str, text1: str, text2: str) -> str | None:
    """Compose captions with Pillow only; no display or temporary EPS file is required."""
    try:
        source = Image.open(BytesIO(convert_binary(binary, "PIL"))).convert("RGB")
        bottom_label = _caption_image(text1, _font(30), source.width) if text1 else None
        right_label = _caption_image(text2, _font(27), source.height).rotate(90, expand=True) if text2 else None

        left_width = max(source.width, bottom_label.width if bottom_label else 0)
        top_height = max(source.height, right_label.height if right_label else 0)
        right_width = right_label.width if right_label else 0
        bottom_height = bottom_label.height if bottom_label else 0
        canvas = Image.new("RGB", (left_width + right_width, top_height + bottom_height), "white")

        source_position = ((left_width - source.width) // 2, (top_height - source.height) // 2)
        canvas.paste(source, source_position)
        if bottom_label:
            canvas.paste(bottom_label, ((left_width - bottom_label.width) // 2, top_height))
        if right_label:
            canvas.paste(right_label, (left_width, (top_height - right_label.height) // 2))
        output = BytesIO()
        canvas.save(output, format="JPEG", quality=90)
        return convert_binary(output.getvalue(), "string")
    except Exception:
        return None


def get_pokemon_image(
    key: str,
    settings: dict[str, object],
    image_path: str | Path,
    height: str,
    weight: str,
    binary: object | None = None,
    text2: str | None = None,
) -> str | None:
    try:
        source = Path(image_path).read_bytes() if binary is None else binary
        caption = text2 or ""
        if text2 is None:
            values: list[str] = []
            if settings.get("show_pokemon_height") and height:
                values.append(height)
            if settings.get("show_pokemon_weight") and weight:
                values.append(weight)
            caption = "  ".join(values)
        return get_image(source, key, key, caption)
    except Exception:
        return None


def get_image_binary(path: str | Path) -> str | None:
    try:
        with Image.open(Path(path)) as image:
            output = BytesIO()
            image.save(output, format=image.format)
        return convert_binary(output.getvalue(), "string")
    except Exception:
        return None
