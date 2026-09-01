# Darktide Fixes Utility Design

## Purpose

Build a safe, repeatable Python command-line utility that discovers a Steam installation of Warhammer 40,000: Darktide on Linux and applies the selected non-graphical configuration fixes. The utility must not edit Steam account configuration or change in-game graphics settings.

## Project and tooling

- Project location: `/home/egor/repos/game-fixes`
- Main script: `wh40k_darktide_fixes.py`
- Python environment and commands managed with `uv`
- Runtime implementation uses only the Python standard library
- Tests use `pytest` as a development dependency
- A local `.venv` is created through `uv`

## Configuration changes

The utility modifies Darktide's `bundle/application_settings/win32_settings.ini` so these effective assignments are present:

```ini
streaming_buffer_size = 128
streaming_texture_pool_size = 1024
```

It modifies `bundle/application_settings/settings_common.ini` so the relevant streamer assignments have these values:

```ini
feedback_buffer_size = 16
max_age_out_tiles_per_frame = 16
max_streaming_tiles_per_frame = 16
max_texture_pool_size = 1024
staging_buffer_size = 8
threaded_streamer = true
tile_age_out_time_ms = 5000
tile_staging_buffer_size = 64
streaming_buffer_size = 128
streaming_max_open_streams = 32
streaming_texture_pool_size = 1024
```

Existing threshold settings remain unchanged. Every applicable `streaming_buffer_size` and `streaming_texture_pool_size` assignment in the intended top-level and `texture_streamer_settings` portions is updated, without changing unrelated settings.

The utility modifies the Proton prefix's `user_settings.config` so the `threads` block contains:

```text
worker_threads = 8
```

It does not change ray tracing, upscaling, frame generation, texture quality, shadows, effects, resolution, frame limiting, or other in-game settings.

After a successful apply, it prints the Steam launch option for the user to copy manually:

```text
%command% --lua-heap-mb-size 2048
```

The utility never edits Steam's `localconfig.vdf`.

## Discovery

Darktide is identified by Steam application ID `1361210`.

Discovery first reads Steam `libraryfolders.vdf` files beneath common per-user Steam roots and extracts configured library paths. It then checks each library for:

```text
steamapps/common/Warhammer 40,000 DARKTIDE
steamapps/compatdata/1361210
```

Fallback discovery scans a bounded set of conventional locations beneath the current user's home directory, including `.local/share/Steam`, `.steam`, and directories named `SteamLibrary`. It does not recursively scan the entire filesystem.

The currently observed installation is:

```text
/home/egor/games/SteamLibrary/steamapps/common/Warhammer 40,000 DARKTIDE
/home/egor/games/SteamLibrary/steamapps/compatdata/1361210
```

Explicit `--game-dir` and `--compatdata-dir` arguments override automatic discovery. Paths are resolved and validated before use. The game and compatdata locations may reside in different Steam libraries.

If discovery finds multiple valid installations and no explicit override resolves the ambiguity, the utility lists them and exits without modifying anything.

## Command-line behavior

The supported modes are:

```text
uv run python wh40k_darktide_fixes.py --dry-run
uv run python wh40k_darktide_fixes.py --apply
uv run python wh40k_darktide_fixes.py --restore
```

`--dry-run` performs discovery, validates files, computes changes, and prints a concise diff without writing. `--apply` creates backups and atomically applies all validated changes. `--restore` restores the most recent complete backup set created by the utility.

Exactly one mode is required. Explicit path overrides can accompany any mode. Help output documents all arguments and examples.

## Safety and transaction model

Before an apply, all target files must exist, be readable, contain the expected structural markers, and produce a meaningful proposed result. Validation is completed for every target before any target is written.

Backups are stored beneath the project in a timestamped directory under `.backups/`. Each backup set contains the original files plus a JSON manifest recording original absolute paths, hashes, timestamp, and tool version. `.backups/` is ignored by Git.

Writes use a temporary sibling file followed by an atomic replacement. Original permission bits are preserved. If a write fails after earlier files were replaced, the utility rolls those files back from the current backup set and reports the failure.

Running `--apply` repeatedly is idempotent: already-correct values produce no duplicate settings and no unnecessary backup. Unknown or malformed layouts cause a clear error and no writes.

`--restore` validates the backup manifest and hashes, restores every member of the most recent complete backup set atomically, and keeps the backup set available for later inspection.

## Internal structure

The single requested script remains the executable entry point, but its logic is divided into small functions:

- CLI parsing and mode selection
- Steam VDF library-path extraction
- installation and config discovery
- narrowly scoped configuration transformations
- validation and diff presentation
- backup manifest creation and selection
- atomic application, rollback, and restore

Pure discovery and transformation functions accept paths or strings and return structured results. Filesystem mutation is confined to the backup/apply/restore functions, enabling tests to exercise behavior in temporary directories.

## Errors and reporting

Expected user-facing failures include missing installations, ambiguous installations, missing target files, malformed configuration structures, permission failures, incomplete backups, and unchanged source layouts that no longer match known Darktide configuration formats.

Errors identify the affected path and corrective action, then exit nonzero. Dry-run and apply output list every discovered target. A successful apply reports changed files, backup location, and the launch option. It warns that Steam updates or file verification may overwrite game-directory changes.

## Testing and acceptance

Development follows test-driven development. Tests use temporary fake Steam libraries and representative minimal configuration fixtures; they never modify the real game installation.

Coverage includes:

- parsing escaped and unescaped Steam library paths
- detecting the observed multi-library layout
- explicit path overrides
- ambiguous and missing installations
- each required configuration transformation
- preservation of unrelated settings and threshold values
- malformed and already-correct input
- idempotent repeated application
- dry-run producing no writes
- timestamped backups and manifests
- atomic apply rollback on a simulated failure
- restoring the latest complete backup
- launch-option output only after successful apply

Acceptance requires a clean test run, a successful dry-run against the real Darktide installation, and inspection of its diff. Actual application to the real installation occurs only after the dry-run succeeds.
