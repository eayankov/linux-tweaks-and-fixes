# Darktide performance fixes

This standard-library-only utility safely previews and applies a small set of
non-graphical Warhammer 40,000: Darktide configuration fixes. It changes only
the three validated configuration files below; it never edits Steam's
`localconfig.vdf` or graphical/in-game quality settings.

## Setup and usage

```bash
uv sync
uv run pytest -v
uv run python wh40k_darktide_fixes.py --dry-run
uv run python wh40k_darktide_fixes.py --apply
uv run python wh40k_darktide_fixes.py --restore
```

Always start with `--dry-run`. It automatically searches the standard Steam
locations and direct `SteamLibrary` directories below your home directory,
validates all three target files, and prints a unified diff without writing
anything. Use explicit paths for an advanced or test installation:

```bash
uv run python wh40k_darktide_fixes.py --dry-run \
  --game-dir /path/to/steamapps/common/'Warhammer 40,000 DARKTIDE' \
  --compatdata-dir /path/to/steamapps/compatdata/1361210
```

`--home /path/to/home` overrides the home directory used by automatic
discovery. The game and compatibility-prefix overrides can also be used with
`--apply`.

Every managed performance value has a command-line option. Existing tuning
values remain the defaults, so commands without tuning options retain their
previous behavior. Run `--help` for the complete list. For a high-VRAM
diagnostic profile that restores the feedback-streamer throughput defaults and
changes only its texture-pool capacity, preview and apply:

```bash
uv run python wh40k_darktide_fixes.py --dry-run \
  --feedback-buffer-size 4 \
  --max-age-out-tiles-per-frame 64 \
  --max-streaming-tiles-per-frame 64 \
  --max-texture-pool-size 2048 \
  --staging-buffer-size 4 \
  --tile-staging-buffer-size 4

uv run python wh40k_darktide_fixes.py --apply \
  --feedback-buffer-size 4 \
  --max-age-out-tiles-per-frame 64 \
  --max-streaming-tiles-per-frame 64 \
  --max-texture-pool-size 2048 \
  --staging-buffer-size 4 \
  --tile-staging-buffer-size 4
```

Change one profile at a time and restart Darktide between comparisons. The
engine does not publicly document the units of these settings.

## What it changes

The script validates the expected layout before changing values in:

- `bundle/application_settings/win32_settings.ini`
- `bundle/application_settings/settings_common.ini`
- `pfx/drive_c/users/steamuser/AppData/Roaming/Fatshark/Darktide/user_settings.config`

The user-settings change sets the worker-thread value to `8`. It intentionally
does not change resolution, quality presets, textures, shadows, ray tracing,
upscaling, frame generation, or other graphical settings.

## Safety and recovery

`--apply` first makes a complete timestamped backup under `.backups/` beside
the script, then atomically replaces the validated files. If no changes are
pending, it reports that state and creates no backup. `--restore` restores the
most recent complete backup created by the utility and does not need Steam
discovery.

Steam updates or **Verify integrity of game files** can overwrite changes in
the game directory. Keep the backup until you are satisfied and repeat a
dry-run after an update or verification.

After a successful apply, enter this launch option manually in Darktide's
Steam launch-options field:

```text
%command% --lua-heap-mb-size 2048
```
