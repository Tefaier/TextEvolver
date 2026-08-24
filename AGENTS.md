## What this is

A Flask web app ("TextEvolver") that rewrites ebook/document files. A user creates a *Setting* (unit conversions, phrase replacements, image insertion rules, Pokémon fandom support), uploads `.docx` / `.epub` / `.html` / `.fb2` files, and gets back a zip of files where measurements are converted, phrases replaced, and images injected next to matching words.

## Commands

There is no `requirements.txt`, test suite, or linter config. Dependencies must be installed manually:

```bash
pip install flask flask-sqlalchemy flask-migrate flask-login flask-wtf wtforms \
            psutil beautifulsoup4 lxml python-docx EbookLib \
            selenium seleniumbase Pillow requests
```

```bash
python server.py                      # run web server (port 5000) + background job dispatcher

FLASK_APP=web_app flask db upgrade    # apply migrations
FLASK_APP=web_app flask db migrate -m "msg"   # autogenerate a migration
```

`python process_manager.py` has a `__main__` block for standalone file processing, but it is stale (calls `files_processing` with 2 args instead of 4) — fix the call before using it.

## Environment

- `SECRET_KEY` — falls back to a hard-coded dev key ([web_app/config.py:5](web_app/config.py#L5)).
- `DATABASE_URL` — falls back to `sqlite:///app.db` at repo root.
- `CHROME_DIRECTORY` — Chrome user-data dir for the Selenium driver.
- `work_directory` in [web_app/server_functions.py:23](web_app/server_functions.py#L23) is a **hard-coded Windows absolute path** and must be edited to run anywhere else. Related: [process_manager.files_processing](process_manager.py#L276) does `directory.replace('/', '\\')` and joins paths with `\\`, so file staging is Windows-only as written.

## Architecture

### Two-process split

[server.py](server.py) starts the Flask app **and** a separate `background_processer` process. They communicate only through the database:

- Web request → `initiate_process()` writes a `Thread` row (`waits=True`) and saves uploaded files to `work_directory/<user_id>/origin_files/`.
- `background_processer` ([web_app/server_functions.py:30](web_app/server_functions.py#L30)) polls every 10 s for `waits=True` rows, and when `psutil.virtual_memory().free` exceeds `save_memory_limit` spawns a `Process(target=files_processing)`, then flips `waits=False` and stores the OS pid in `Thread.ident`.
- Cancellation is `os.kill(ident, SIGABRT)` ([terminate_process](web_app/server_functions.py#L71)); the worker catches `KeyboardInterrupt` to requeue itself.
- The worker deletes its own `Thread` row when done — so a user's job state is "row exists → in flight", "no row + files in `new_files/` → ready to download".
- Worker processes need `with web_app.app.app_context():` around any DB access.

### Processing pipeline

`files_processing(settings_id, user_id, dir, new_dir)` → builds one `ProcessUnit`, then calls `remake_text(from, to)` per file.

1. **`ProcessUnit.__init__`** ([process_manager.py:117](process_manager.py#L117)): `values_reset()` clears all state, then [settings_control.get_settings](settings_control.py#L8) hydrates it from the `Setting` row into plain dicts (`units_list`, `word_conversions`, `direct_conversions`, `extra_img_list`, `pokemons_list`). If the Pokémon fandom is active, this **scrapes pokemondb.net at construction time** via Selenium.
2. **`remake_text`** dispatches on file extension. html/fb2 parse with BeautifulSoup (`html.parser` / `xml`), epub via `ebooklib`, docx via `python-docx`. All four converge on walking `p`/`span` (or paragraphs and table cells) and calling `p_process` / `p_process_word`. The source file is deleted after writing the output.
3. Per text unit: `direct_replace` (literal swaps + optional UTF normalization) → `images_locate` (regex over the cleaned text for image/Pokémon trigger words) → `text_alteration` (unit and phrase conversion).

### The text-matching model (text_analysis.py)

This is the subtle part. Matching never happens on the raw string:

- `text_cleaner()` produces a punctuation-stripped copy, respecting the `coma in digits` setting (comma vs period as decimal separator).
- `find_in_clean()` searches the *cleaned* words and returns a `replace_map`: a list of `[original_clean_word, replacement]` pairs covering the whole string.
- `text_modifier()` fills in replacements, matching the original's capitalization, and — for unit conversions — looks back up to `digit_len_before` (7) words, runs `text2int()` to turn spelled-out numbers ("twenty-three and a half", `5'11`) into floats, and scales them.
- `replace_iteration()` then walks the *original* (uncleaned) words right-to-left and splices replacements back in, so punctuation is preserved. It raises if the map can't be aligned; callers wrap it in bare `try/except` and silently keep the original text.

`possible_mutations` handles suffixes (plural/possessive, incl. Russian endings) so a trigger word matches its inflections.

### Images

- Stored in `ImageConv.images` as base64 strings joined by `*`. `binary_converter.convert_binary(data, "PIL"|"binary"|"string")` is the single conversion point between base64 str, raw bytes, and PIL-ready bytes.
- [image_controller.get_image](image_controller.py#L15) composites the image with caption text using a **tkinter Canvas**, exports via `canvas.postscript()` to an `.eps` on disk, reopens it with PIL, and re-encodes to JPEG. This needs a display and writes temp files into the CWD; it returns `None` on any failure.
- `get_pokemon_image` scrapes the Pokémon page for the artwork URL plus height/weight, then feeds it to `get_image`.
- Insertion is format-specific (`image_insert`): fb2/epub add a `<binary>` element plus `<image href="#n">`, docx adds a centered paragraph with an inline picture, html adds a base64 `data:` `<img>`.

### Selenium

All driver construction goes through [web_settings.build_driver()](web_settings.py#L9) (seleniumbase undetected-Chrome, headless, incognito). Change driver options there, not at call sites.

## Conventions and known rough edges

- `ProcessUnit` methods use `this` instead of `self`.
- Failure handling is overwhelmingly bare `try/except` returning `None`/`False`; when debugging "nothing happened", suspect a swallowed exception rather than a missing code path.
- Form validation in [collect_values](web_app/server_functions.py#L175) calls `eval()` on submitted checkbox/boolean fields, and passwords are stored and compared in plaintext ([routes.py:136](web_app/routes.py#L136), [models.py:85](web_app/models.py#L85)). Both are pre-existing; don't copy the pattern into new code.
- `User.settings` uses a two-column `primaryjoin` on both `id` and `name`, so `Setting.owner_id` and `Setting.owner_name` must stay in sync.
