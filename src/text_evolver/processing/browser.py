from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from seleniumbase import Driver
from seleniumbase.core.sb_driver import DriverMethods

from text_evolver.config import get_application_settings

HTML_IMAGE_STYLE = "display: block; margin-left: auto; margin-right: auto; max-width: 99%;"


@contextmanager
def browser_session() -> Generator[DriverMethods, None, None]:
    settings = get_application_settings()
    options: dict[str, Any] = {
        "uc": True,
        "headless": True,
        "incognito": True,
        "browser": "chrome",
        "use_auto_ext": False,
        "disable_features": (
            "LensOverlay,TranslateUI,Translate,OptimizationGuideModelDownloading,"
            "OptimizationHintsFetching,OptimizationTargetPrediction,OptimizationHints"
        ),
        "page_load_strategy": "eager",
        "locale_code": "en",
        "d_width": 1920,
        "d_height": 1080,
        "chromium_arg": "--disable-dev-shm-usage,--no-sandbox",
    }
    if settings.chrome_binary:
        options["binary_location"] = settings.chrome_binary
    driver = None
    try:
        driver = Driver(**options)
        yield driver
    finally:
        if driver is not None:
            driver.quit()
