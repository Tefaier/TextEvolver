from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from PIL import Image
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from seleniumbase.core.sb_driver import DriverMethods

from text_evolver.fandoms import FandomName
from text_evolver.processing.browser import browser_session

LOGGER = logging.getLogger("text_evolver.processing.pokemon_cache")

POKEMON_BASE_URL = "https://pokemondb.net"
POKEMON_LIST_PATH = "/pokedex/all"
POKEMON_CSV_NAME = "pokemon.csv"
CSV_FIELDS = ("name", "page_url", "image_url", "image_file", "height", "weight")


@dataclass(frozen=True)
class PokemonListEntry:
    name: str
    page_url: str


@dataclass(frozen=True)
class PokemonRecord:
    name: str
    page_url: str
    image_url: str
    image_path: Path
    height: str
    weight: str


@dataclass(frozen=True)
class PokemonCacheRefresh:
    listed: int
    retained: int
    downloaded: int
    failed: int


def pokemon_cache_directory(temp_root: Path) -> Path:
    return (temp_root / FandomName.POKEMONS).resolve()


def _record_key(name: str, page_url: str) -> tuple[str, str]:
    '''Makes it safe for find and comparison'''
    return name.casefold(), page_url


def _safe_image_filename(entry: PokemonListEntry) -> str:
    '''Constructs name for file as safe for filesystem'''
    slug = re.sub(r"[^a-z0-9]+", "-", entry.name.casefold()).strip("-") or "pokemon"
    digest = hashlib.sha256(f"{entry.name}\0{entry.page_url}".encode()).hexdigest()[:6]
    return f"{slug[:60]}-{digest}.image"


def _parse_pokemon_list(html: str) -> tuple[PokemonListEntry, ...]:
    '''Returns list of located entries safe for further scraping'''
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("tbody")
    if body is None:
        raise RuntimeError("Pokémon list page does not contain a table body")
    entries: dict[tuple[str, str], PokemonListEntry] = {}
    for row in body.find_all("tr"):
        name_field = row.find(class_="cell-name")
        if name_field is None or (link_field := name_field.find("a")) is None:
            continue
        href = link_field.get("href")
        if not isinstance(href, str) or not href:
            continue
        muted = name_field.find(class_="text-muted")
        name = link_field.get_text(strip=True).replace("♀", "").replace("♂", "")
        if muted is not None and name in muted.get_text():
            name = muted.get_text(strip=True).replace("♀", "").replace("♂", "")
        if not name:
            continue
        entry = PokemonListEntry(name=name, page_url=urljoin(POKEMON_BASE_URL, href))
        entries.setdefault(_record_key(entry.name, entry.page_url), entry)
    if not entries:
        raise RuntimeError("Pokémon list page does not contain any Pokémon")
    return tuple(entries.values())


def _detail_value(driver: DriverMethods, label: str) -> str:
    '''Get value of height or weight from opened page if present'''
    try:
        value = driver.find_element(
            By.XPATH,
            "//div[@class='sv-tabs-panel-list']//following-sibling::div[@class='sv-tabs-panel active']"
            f"//th[text()='{label}']//parent::tr//td",
        )
    except NoSuchElementException:
        return ""
    return str(value.text).rsplit("(", 1)[0].strip()


def _fetch_record(
    driver: DriverMethods,
    http: requests.Session,
    entry: PokemonListEntry,
    cache_directory: Path,
) -> PokemonRecord:
    '''Gets and writes image and extra info if located '''
    driver.get(entry.page_url)
    try:
        tab = driver.find_element(By.XPATH, f"//div[@class='sv-tabs-tab-list']/a[text()='{entry.name}']")
        if tab.is_displayed():
            tab.click()
    except NoSuchElementException:
        pass
    artwork = driver.find_element(
        By.XPATH,
        "//div[@class='sv-tabs-panel-list']//following-sibling::div[@class='sv-tabs-panel active']//a",
    ).get_attribute("href")
    if not artwork:
        raise RuntimeError(f"No artwork URL found for {entry.name}")
    response = http.get(artwork, timeout=30)
    response.raise_for_status()
    with Image.open(BytesIO(response.content)) as image:
        image.verify()

    images_directory = cache_directory / "images"
    images_directory.mkdir(parents=True, exist_ok=True)
    image_path = images_directory / _safe_image_filename(entry)
    temporary_path = image_path.with_suffix(".tmp")
    try:
        temporary_path.write_bytes(response.content)
        temporary_path.replace(image_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return PokemonRecord(
        name=entry.name,
        page_url=entry.page_url,
        image_url=artwork,
        image_path=image_path,
        height=_detail_value(driver, "Height"),
        weight=_detail_value(driver, "Weight"),
    )


def _read_records(cache_directory: Path) -> tuple[PokemonRecord, ...]:
    '''Reads existing info from csv with filters on data being present, valid, and secure (path)'''
    csv_path = cache_directory / POKEMON_CSV_NAME
    if not csv_path.is_file():
        return ()
    records: list[PokemonRecord] = []
    cache_root = cache_directory.resolve()
    try:
        dataframe = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    except (OSError, UnicodeDecodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
        LOGGER.warning("Ignoring unreadable Pokémon cache CSV: %s", csv_path, exc_info=True)
        return ()
    if not set(CSV_FIELDS).issubset(dataframe.columns):
        LOGGER.warning("Ignoring Pokémon cache with an invalid CSV header: %s", csv_path)
        return ()
    for row in dataframe.loc[:, list(CSV_FIELDS)].to_dict(orient="records"):
        image_file_value = row["image_file"].strip()
        name = row["name"].strip()
        page_url = row["page_url"].strip()
        if not image_file_value or not name or not page_url:
            continue
        image_file = Path(image_file_value)
        image_path = (cache_directory / image_file).resolve()
        if image_file.is_absolute() or not image_path.is_relative_to(cache_root) or not image_path.is_file():
            continue
        records.append(
            PokemonRecord(
                name=name,
                page_url=page_url,
                image_url=row["image_url"].strip(),
                image_path=image_path,
                height=row["height"].strip(),
                weight=row["weight"].strip(),
            )
        )
    return tuple(records)


def load_pokemon_cache(temp_root: Path) -> tuple[PokemonRecord, ...]:
    return _read_records(pokemon_cache_directory(temp_root))


def _write_records(cache_directory: Path, records: tuple[PokemonRecord, ...]) -> None:
    '''Writes records to csv fully replacing what was there previously'''
    csv_path = cache_directory / POKEMON_CSV_NAME
    temporary_path = csv_path.with_suffix(".csv.tmp")
    cache_directory.mkdir(parents=True, exist_ok=True)
    try:
        rows = [
            {
                "name": record.name,
                "page_url": record.page_url,
                "image_url": record.image_url,
                "image_file": str(record.image_path.relative_to(cache_directory)),
                "height": record.height,
                "weight": record.weight,
            }
            for record in sorted(records, key=lambda value: (value.name.casefold(), value.page_url))
        ]
        pd.DataFrame.from_records(rows, columns=CSV_FIELDS).to_csv(temporary_path, index=False, encoding="utf-8")
        temporary_path.replace(csv_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def refresh_pokemon_cache(temp_root: Path) -> PokemonCacheRefresh:
    '''Refreshes pokemon info in temp directory - loads pages only for diff'''
    cache_directory = pokemon_cache_directory(temp_root)
    cache_directory.mkdir(parents=True, exist_ok=True)
    existing = {_record_key(value.name, value.page_url): value for value in _read_records(cache_directory)}
    with browser_session() as driver:
        driver.get(POKEMON_BASE_URL + POKEMON_LIST_PATH)
        entries = _parse_pokemon_list(driver.page_source)
        # from existing records with possible name change if just by case
        records = {
            _record_key(entry.name, entry.page_url): replace(
                existing[_record_key(entry.name, entry.page_url)],
                name=entry.name,
                page_url=entry.page_url,
            )
            for entry in entries
            if _record_key(entry.name, entry.page_url) in existing
        }
        retained = len(records)
        downloaded = 0
        failed = 0
        with requests.Session() as http:
            for entry in entries:
                key = _record_key(entry.name, entry.page_url)
                if key in records:
                    continue
                try:
                    records[key] = _fetch_record(driver, http, entry, cache_directory)
                except Exception:
                    failed += 1
                    LOGGER.exception("Unable to cache Pokémon %s", entry.name)
                    continue
                downloaded += 1
    _write_records(cache_directory, tuple(records.values()))
    return PokemonCacheRefresh(len(entries), retained, downloaded, failed)
