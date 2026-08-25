from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
from PIL import Image

from text_evolver.processing import browser, images, pokemon_cache


def write_image(path, color="green"):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (20, 20), color)
    image.save(path, format="PNG")


def test_refresh_only_fetches_pokemon_missing_from_cache(monkeypatch, tmp_path):
    cache_directory = pokemon_cache.pokemon_cache_directory(tmp_path)
    bulbasaur_image = cache_directory / "images" / "bulbasaur.image"
    write_image(bulbasaur_image)
    existing = pokemon_cache.PokemonRecord(
        name="Bulbasaur",
        page_url="https://pokemondb.net/pokedex/bulbasaur",
        image_url="https://img.example/bulbasaur.png",
        image_path=bulbasaur_image,
        height="0.7 m",
        weight="6.9 kg",
    )
    pokemon_cache._write_records(cache_directory, (existing,))

    list_html = """
        <table><tbody>
          <tr><td class="cell-name"><a href="/pokedex/bulbasaur">Bulbasaur</a></td></tr>
          <tr><td class="cell-name"><a href="/pokedex/ivysaur">Ivysaur</a></td></tr>
        </tbody></table>
    """

    class FakeDriver:
        page_source = list_html

        def get(self, _url):
            return None

    @contextmanager
    def fake_browser_session():
        yield FakeDriver()

    fetched = []

    def fake_fetch(_driver, _http, entry, directory):
        fetched.append(entry.name)
        image_path = directory / "images" / "ivysaur.image"
        write_image(image_path, "blue")
        return pokemon_cache.PokemonRecord(
            name=entry.name,
            page_url=entry.page_url,
            image_url="https://img.example/ivysaur.png",
            image_path=image_path,
            height="1.0 m",
            weight="13.0 kg",
        )

    monkeypatch.setattr(pokemon_cache, "browser_session", fake_browser_session)
    monkeypatch.setattr(pokemon_cache, "_fetch_record", fake_fetch)

    first = pokemon_cache.refresh_pokemon_cache(tmp_path)
    second = pokemon_cache.refresh_pokemon_cache(tmp_path)
    ivysaur = next(record for record in pokemon_cache.load_pokemon_cache(tmp_path) if record.name == "Ivysaur")
    ivysaur.image_path.unlink()
    third = pokemon_cache.refresh_pokemon_cache(tmp_path)

    assert first == pokemon_cache.PokemonCacheRefresh(listed=2, retained=1, downloaded=1, failed=0)
    assert second == pokemon_cache.PokemonCacheRefresh(listed=2, retained=2, downloaded=0, failed=0)
    assert third == pokemon_cache.PokemonCacheRefresh(listed=2, retained=1, downloaded=1, failed=0)
    assert fetched == ["Ivysaur", "Ivysaur"]
    records = pokemon_cache.load_pokemon_cache(tmp_path)
    assert {record.name for record in records} == {"Bulbasaur", "Ivysaur"}
    dataframe = pd.read_csv(cache_directory / pokemon_cache.POKEMON_CSV_NAME, dtype=str, keep_default_na=False)
    assert tuple(dataframe.columns) == pokemon_cache.CSV_FIELDS


def test_pokemon_image_uses_cached_file_and_csv_values(monkeypatch, tmp_path):
    image_path = tmp_path / "pikachu.image"
    write_image(image_path, "yellow")
    captured = {}

    def fake_get_image(source, name, text1, text2):
        captured.update(source=source, name=name, text1=text1, text2=text2)
        return "encoded-image"

    monkeypatch.setattr(images, "get_image", fake_get_image)

    result = images.get_pokemon_image(
        "Pikachu",
        {"show_pokemon_height": True, "show_pokemon_weight": True},
        image_path,
        "0.4 m",
        "6.0 kg",
    )

    assert result == "encoded-image"
    assert captured == {
        "source": image_path.read_bytes(),
        "name": "Pikachu",
        "text1": "Pikachu",
        "text2": "0.4 m  6.0 kg",
    }


def test_browser_session_does_not_set_custom_profile(monkeypatch):
    options = {}

    class FakeDriver:
        def quit(self):
            return None

    def fake_driver(**kwargs):
        options.update(kwargs)
        return FakeDriver()

    monkeypatch.setattr(browser, "Driver", fake_driver)
    monkeypatch.setattr(browser, "get_application_settings", lambda: SimpleNamespace(chrome_binary=None))

    with browser.browser_session():
        pass

    assert "user_data_dir" not in options
