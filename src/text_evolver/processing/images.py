from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from text_evolver.processing.binary_converter import convert_binary
from text_evolver.processing.browser import build_driver


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def get_image(binary: object, name: str, text1: str, text2: str) -> str | None:
    """Compose captions with Pillow only; no display or temporary EPS file is required."""
    try:
        source = Image.open(BytesIO(convert_binary(binary, "PIL"))).convert("RGB")
        bottom_height = 44 if text1 else 0
        right_width = 44 if text2 else 0
        canvas = Image.new("RGB", (source.width + right_width, source.height + bottom_height), "white")
        canvas.paste(source, (0, 0))
        draw = ImageDraw.Draw(canvas)
        if text1:
            font = _font(30)
            bounds = draw.textbbox((0, 0), text1, font=font)
            width = bounds[2] - bounds[0]
            draw.text(((source.width - width) / 2, source.height + 4), text1, fill="black", font=font)
        if text2:
            font = _font(27)
            bounds = draw.textbbox((0, 0), text2, font=font)
            label = Image.new("RGBA", (bounds[2] - bounds[0] + 10, bounds[3] - bounds[1] + 10), "white")
            ImageDraw.Draw(label).text((5, 5), text2, fill="black", font=font)
            label = label.rotate(90, expand=True)
            canvas.paste(label.convert("RGB"), (source.width, max(0, (source.height - label.height) // 2)))
        output = BytesIO()
        canvas.save(output, format="JPEG", quality=90)
        return convert_binary(output.getvalue(), "string")
    except Exception:
        return None


def get_pokemon_image(
    key: str,
    settings: dict[str, object],
    link: str,
    binary: object | None = None,
    text2: str | None = None,
) -> str | None:
    driver = None
    try:
        driver = build_driver()
        driver.get(link)
        try:
            tab = driver.find_element(By.XPATH, f"//div[@class='sv-tabs-tab-list']/a[text()='{key}']")
            if tab.is_displayed():
                tab.click()
        except NoSuchElementException:
            pass
        artwork_link = driver.find_element(
            By.XPATH,
            "//div[@class='sv-tabs-panel-list']//following-sibling::div[@class='sv-tabs-panel active']//a",
        ).get_attribute("href")
        response = requests.get(artwork_link, timeout=30)
        response.raise_for_status()
        caption = text2 or ""
        if text2 is None:
            values: list[str] = []
            if settings.get("show_pokemon_height"):
                value = driver.find_element(
                    By.XPATH,
                    "//div[@class='sv-tabs-panel-list']//following-sibling::div[@class='sv-tabs-panel active']"
                    "//th[text()='Height']//parent::tr//td",
                )
                values.append(str(value.text).rsplit("(", 1)[0].strip())
            if settings.get("show_pokemon_weight"):
                value = driver.find_element(
                    By.XPATH,
                    "//div[@class='sv-tabs-panel-list']//following-sibling::div[@class='sv-tabs-panel active']"
                    "//th[text()='Weight']//parent::tr//td",
                )
                values.append(str(value.text).rsplit("(", 1)[0].strip())
            caption = "  ".join(values)
        return get_image(response.content if binary is None else binary, key, key, caption)
    except Exception:
        return None
    finally:
        if driver is not None:
            driver.quit()


def get_image_binary(path: str | Path) -> str | None:
    try:
        with Image.open(Path(path)) as image:
            output = BytesIO()
            image.save(output, format=image.format)
        return convert_binary(output.getvalue(), "string")
    except Exception:
        return None

