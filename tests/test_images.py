from io import BytesIO
from pathlib import Path

from PIL import Image

from text_evolver.processing.binary_converter import convert_binary
from text_evolver.processing.images import _caption_image, _font, _wrap_text, get_image


def _source_image(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "blue").save(output, format="PNG")
    return output.getvalue()


def _decoded_image(value: str) -> Image.Image:
    return Image.open(BytesIO(convert_binary(value, "PIL")))


def test_caption_wraps_using_rendered_width():
    font = _font(20)

    wrapped = _wrap_text("Bulbasaur is a very leafy Pokemon", font, 100)

    assert len(wrapped.splitlines()) > 1
    assert all(_caption_image(line, font, 100).width <= 100 for line in wrapped.splitlines())


def test_get_image_adds_only_measured_caption_space():
    source_width = 160
    source_height = 90
    bottom_text = "A centered caption that wraps onto several lines"
    side_text = "Height 0.7 m Weight 6.9 kg"
    bottom_label = _caption_image(bottom_text, _font(30), source_width)
    right_label = _caption_image(side_text, _font(27), source_height).rotate(90, expand=True)

    result = get_image(_source_image(source_width, source_height), "Bulbasaur", bottom_text, side_text)

    assert result is not None
    with _decoded_image(result) as image:
        assert image.size == (
            max(source_width, bottom_label.width) + right_label.width,
            max(source_height, right_label.height) + bottom_label.height,
        )


def test_get_image_does_not_expand_without_captions():
    result = get_image(_source_image(80, 50), "unused", "", "")

    assert result is not None
    with _decoded_image(result) as image:
        assert image.size == (80, 50)


def test_get_image_reads_a_staged_local_file(tmp_path: Path):
    source = tmp_path / "staged.png"
    source.write_bytes(_source_image(64, 48))

    result = get_image(source, "unused", "", "")

    assert result is not None
    with _decoded_image(result) as image:
        assert image.size == (64, 48)
