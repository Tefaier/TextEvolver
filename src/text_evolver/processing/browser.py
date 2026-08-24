from typing import Any

from seleniumbase import Driver
from seleniumbase.core.sb_driver import DriverMethods

from text_evolver.config import get_settings

HTML_IMAGE_STYLE = "display: block; margin-left: auto; margin-right: auto; max-width: 99%;"
POKEMON_BASE_URL = "https://pokemondb.net"
POKEMON_LIST_PATH = "/pokedex/all"


def build_driver() -> DriverMethods:
    settings = get_settings()
    options: dict[str, Any] = {
        "uc": True,
        "headless": True,
        "incognito": True,
        "user_data_dir": str(settings.chrome_user_data_dir),
        "browser": "chrome",
        "use_auto_ext": False,
        "disable_features": (
            "LensOverlay,TranslateUI,Translate,OptimizationGuideModelDownloading,"
            "OptimizationHintsFetching,OptimizationTargetPrediction,OptimizationHints"
        ),
        "page_load_strategy": "none",
        "locale_code": "en",
        "d_width": 1920,
        "d_height": 1080,
        "chromium_arg": "--disable-dev-shm-usage,--no-sandbox",
    }
    if settings.chrome_binary:
        options["binary_location"] = settings.chrome_binary
    return Driver(**options)

