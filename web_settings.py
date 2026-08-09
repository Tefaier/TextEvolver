import os
from typing import Dict

from seleniumbase import Driver
from seleniumbase.core.sb_driver import DriverMethods

chrome_directory = os.getenv("CHROME_DIRECTORY")

def build_driver() -> DriverMethods:
    return Driver(
        uc=True,
        headless=True,
        incognito=True,
        user_data_dir=chrome_directory or "/tmp/.google_chrome_incognito",
        **_build_default_settings(),
    )

def _build_default_settings() -> Dict[str, str]:
    return {
        "browser": "chrome",
        "use_auto_ext": False,
        "disable_features": "LensOverlay,TranslateUI,Translate,OptimizationGuideModelDownloading,OptimizationHintsFetching,OptimizationTargetPrediction,OptimizationHints",
        "page_load_strategy": "none",
        "locale_code": "en",
        "d_width": 1920,
        "d_height": 1080,
        "chromium_arg": "--disable-dev-shm-usage",
    }

html_image_style = "display: block; margin-left: auto; margin-right: auto; max-width: 99%;"
pokemons_link = "https://pokemondb.net"
pokemons_link_list = "/pokedex/all"