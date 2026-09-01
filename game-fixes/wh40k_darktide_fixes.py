"""Locate, modify, back up, and restore Darktide configuration files."""

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


APP_ID = "1361210"
GAME_DIR_NAME = "Warhammer 40,000 DARKTIDE"
BACKUP_VERSION = 1
BACKUP_ROOT = Path(__file__).resolve().parent / ".backups"
_MODE_MASK = stat.S_IMODE(0o7777)

_PATH_RE = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"')
_BACKUP_DIR_RE = re.compile(
    r"^backup-(?P<timestamp>\d{8}T\d{6}\.\d{6}Z)(?:-(?P<suffix>\d+))?$"
)


class ConfigError(Exception):
    """Raised when Darktide's installation cannot be identified safely."""


@dataclass(frozen=True)
class Targets:
    game_dir: Path
    compatdata_dir: Path


@dataclass(frozen=True)
class Change:
    name: str
    path: Path
    before: str
    after: str


@dataclass(frozen=True)
class _SourceFile:
    path: Path
    contents: bytes
    mode: int


@dataclass(frozen=True)
class _BackupFile:
    name: str
    original_path: Path
    backup_path: Path
    sha256: str
    mode: int


def parse_library_paths(text: str) -> list[Path]:
    """Extract Steam library paths from a libraryfolders VDF document."""
    return [Path(value.replace("\\\\", "\\")) for value in _PATH_RE.findall(text)]


def _library_candidates(home: Path) -> list[Path]:
    roots = [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
    ]
    libraries = list(roots)
    for root in roots:
        vdf = root / "steamapps/libraryfolders.vdf"
        if vdf.is_file():
            try:
                contents = vdf.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise ConfigError(f"could not decode Steam library file {vdf}: {error}") from error
            except OSError as error:
                raise ConfigError(f"could not read Steam library file {vdf}: {error}") from error
            libraries.extend(parse_library_paths(contents))
    libraries.extend(home.glob("*/SteamLibrary"))

    unique: list[Path] = []
    for library in libraries:
        resolved = library.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _discover_one(candidates: list[Path], suffix: Path, missing_message: str, multiple_message: str) -> Path:
    matches = [candidate / suffix for candidate in candidates if (candidate / suffix).is_dir()]
    if not matches:
        raise ConfigError(missing_message)
    if len(matches) > 1:
        raise ConfigError(multiple_message)
    return matches[0].resolve()


def _discover_pair(candidates: list[Path]) -> Targets:
    pairs = [
        (
            candidate / "steamapps/common" / GAME_DIR_NAME,
            candidate / "steamapps/compatdata" / APP_ID,
        )
        for candidate in candidates
        if (candidate / "steamapps/common" / GAME_DIR_NAME).is_dir()
        and (candidate / "steamapps/compatdata" / APP_ID).is_dir()
    ]
    if len(pairs) > 1:
        raise ConfigError("multiple Darktide installations were found")
    if pairs:
        game, compat = pairs[0]
        return Targets(game.resolve(), compat.resolve())
    if not any((candidate / "steamapps/common" / GAME_DIR_NAME).is_dir() for candidate in candidates):
        raise ConfigError("Darktide installation was not found")
    raise ConfigError("Darktide game and compatibility prefix were not found in the same Steam library")


def _validate_override(path: Path, label: str) -> Path:
    if not path.is_dir():
        raise ConfigError(f"{label} does not exist: {path}")
    return path.resolve()


def discover_targets(
    home: Path, game_dir: Path | None = None, compatdata_dir: Path | None = None
) -> Targets:
    """Find the sole Darktide game directory and Proton compatibility prefix."""
    candidates = _library_candidates(home)
    if game_dir is None and compatdata_dir is None:
        return _discover_pair(candidates)

    game = (
        _validate_override(game_dir, "game directory")
        if game_dir is not None
        else _discover_one(
            candidates,
            Path("steamapps/common") / GAME_DIR_NAME,
            "Darktide installation was not found",
            "multiple Darktide installations were found",
        )
    )
    compat = (
        _validate_override(compatdata_dir, "compatdata directory")
        if compatdata_dir is not None
        else _discover_one(
            candidates,
            Path("steamapps/compatdata") / APP_ID,
            "Darktide compatibility prefix was not found",
            "multiple Darktide compatibility prefixes were found",
        )
    )
    return Targets(game, compat)


def target_files(targets: Targets) -> dict[str, Path]:
    """Return the three Darktide configuration files managed by this utility."""
    return {
        "win32_settings": targets.game_dir / "bundle/application_settings/win32_settings.ini",
        "settings_common": targets.game_dir / "bundle/application_settings/settings_common.ini",
        "user_settings": targets.compatdata_dir
        / "pfx/drive_c/users/steamuser/AppData/Roaming/Fatshark/Darktide/user_settings.config",
    }


def _assignment_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"^(?P<indent>[ \t]*){re.escape(name)}(?P<equals>[ \t]*=[ \t]*)"
        r"(?P<value>[^\r\n]*)(?P<ending>\r\n|\n|$)",
        re.MULTILINE,
    )


def _validate_assignments(text: str, replacements: dict[str, str]) -> None:
    depths = _brace_depths(text)
    for name in replacements:
        count = sum(
            depths[match.start()] == 0 for match in _assignment_pattern(name).finditer(text)
        )
        if count != 1:
            raise ConfigError(f"expected exactly one direct {name} assignment, found {count}")


def _replace_assignments(text: str, replacements: dict[str, str]) -> str:
    _validate_assignments(text, replacements)
    depths = _brace_depths(text)
    for name, value in replacements.items():
        pattern = _assignment_pattern(name)

        def replace(match: re.Match[str]) -> str:
            if depths[match.start()] != 0:
                return match.group(0)
            return f"{match['indent']}{name}{match['equals']}{value}{match['ending']}"

        text = pattern.sub(replace, text)
        depths = _brace_depths(text)
    return text


def _brace_depths(text: str) -> list[int]:
    depth = 0
    depths: list[int] = []
    for character in text:
        depths.append(depth)
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                raise ConfigError("expected balanced brace blocks")
    if depth != 0:
        raise ConfigError("expected balanced brace blocks")
    return depths


def _top_level_block_matches(text: str, name: str) -> list[re.Match[str]]:
    depths = _brace_depths(text)
    opener = re.compile(rf"^[ \t]*{re.escape(name)}[ \t]*=[ \t]*\{{", re.MULTILINE)
    return [match for match in opener.finditer(text) if depths[match.start()] == 0]


def _named_block(text: str, name: str) -> tuple[int, int]:
    matches = _top_level_block_matches(text, name)
    if len(matches) != 1:
        raise ConfigError(f"expected exactly one top-level {name} block, found {len(matches)}")

    open_brace = matches[0].end() - 1
    depth = 1
    for index in range(open_brace + 1, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return open_brace, index
    raise ConfigError(f"expected a closing brace for {name}")


def _validate_top_level_assignments(text: str, replacements: dict[str, str]) -> None:
    depths = _brace_depths(text)
    for name in replacements:
        matches = [
            match
            for match in _assignment_pattern(name).finditer(text)
            if depths[match.start()] == 0
        ]
        if len(matches) != 1:
            raise ConfigError(
                f"expected exactly one top-level {name} assignment, found {len(matches)}"
            )


def _replace_top_level_assignments(text: str, replacements: dict[str, str]) -> str:
    _validate_top_level_assignments(text, replacements)
    depths = _brace_depths(text)
    for name, value in replacements.items():
        pattern = _assignment_pattern(name)

        def replace(match: re.Match[str]) -> str:
            if depths[match.start()] != 0:
                return match.group(0)
            return f"{match['indent']}{name}{match['equals']}{value}{match['ending']}"

        text = pattern.sub(replace, text)
        depths = _brace_depths(text)
    return text


def _replace_block_assignments(text: str, name: str, replacements: dict[str, str]) -> str:
    open_brace, close_brace = _named_block(text, name)
    body = text[open_brace + 1 : close_brace]
    return (
        text[: open_brace + 1]
        + _replace_assignments(body, replacements)
        + text[close_brace:]
    )


def transform_win32(text: str) -> str:
    replacements = {
        "streaming_buffer_size": "128",
        "streaming_texture_pool_size": "1024",
    }
    win32_blocks = _top_level_block_matches(text, "win32")
    if win32_blocks:
        if len(win32_blocks) != 1:
            raise ConfigError(
                f"expected exactly one top-level win32 block, found {len(win32_blocks)}"
            )
        if _top_level_block_matches(text, "renderer"):
            raise ConfigError("ambiguous win32 configuration layout")
        return _replace_block_assignments(text, "win32", replacements)
    return _replace_block_assignments(
        text,
        "renderer",
        replacements,
    )


def transform_settings_common(text: str) -> str:
    feedback_replacements = {
        "feedback_buffer_size": "16",
        "max_age_out_tiles_per_frame": "16",
        "max_streaming_tiles_per_frame": "16",
        "max_texture_pool_size": "1024",
        "staging_buffer_size": "8",
        "threaded_streamer": "true",
        "tile_age_out_time_ms": "5000",
        "tile_staging_buffer_size": "64",
    }
    texture_replacements = {
        "streaming_buffer_size": "128",
        "streaming_texture_pool_size": "1024",
    }
    top_level_replacements = {
        "streaming_buffer_size": "128",
        "streaming_max_open_streams": "32",
        "streaming_texture_pool_size": "1024",
    }

    feedback_open, feedback_close = _named_block(text, "feedback_streamer_settings")
    texture_open, texture_close = _named_block(text, "texture_streamer_settings")
    _validate_assignments(text[feedback_open + 1 : feedback_close], feedback_replacements)
    _validate_assignments(text[texture_open + 1 : texture_close], texture_replacements)
    _validate_top_level_assignments(text, top_level_replacements)

    text = _replace_block_assignments(text, "feedback_streamer_settings", feedback_replacements)
    text = _replace_block_assignments(text, "texture_streamer_settings", texture_replacements)
    return _replace_top_level_assignments(text, top_level_replacements)


def transform_user_settings(text: str) -> str:
    return _replace_block_assignments(text, "threads", {"worker_threads": "8"})


def plan_changes(targets: Targets) -> list[Change]:
    files = target_files(targets)
    contents: dict[str, str] = {}
    for name, path in files.items():
        if not path.is_file():
            raise ConfigError(f"expected configuration file does not exist: {path}")
        try:
            contents[name] = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as error:
            raise ConfigError(f"could not decode configuration file {path}: {error}") from error
        except OSError as error:
            raise ConfigError(f"could not read configuration file {path}: {error}") from error

    transforms = {
        "win32_settings": transform_win32,
        "settings_common": transform_settings_common,
        "user_settings": transform_user_settings,
    }
    changes: list[Change] = []
    for name, path in files.items():
        before = contents[name]
        after = transforms[name](before)
        if after != before:
            changes.append(Change(name, path, before, after))
    return changes


def sha256_text(text: str) -> str:
    """Return the SHA-256 checksum for UTF-8 configuration text."""
    return _sha256_bytes(text.encode("utf-8"))


def _sha256_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _source_files(changes: list[Change]) -> list[_SourceFile]:
    """Read and validate all sources before a transaction changes anything."""
    sources: list[_SourceFile] = []
    paths: set[Path] = set()
    for change in changes:
        path = change.path.resolve()
        if path in paths:
            raise ConfigError(f"the same configuration file appears more than once: {path}")
        paths.add(path)
        if not path.is_file():
            raise ConfigError(f"expected configuration file does not exist: {path}")
        try:
            contents = path.read_bytes()
            current = contents.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ConfigError(f"could not decode configuration file {path}: {error}") from error
        except OSError as error:
            raise ConfigError(f"could not read configuration file {path}: {error}") from error
        if current != change.before:
            raise ConfigError(f"configuration file changed since planning: {path}")
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as error:
            raise ConfigError(f"could not read mode for configuration file {path}: {error}") from error
        sources.append(_SourceFile(path, contents, mode))
    return sources


def _backup_name(name: str, index: int) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "config"
    return f"{safe_name}-{index:03d}.bak"


def _new_backup_dir(backup_root: Path) -> Path:
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ConfigError(f"could not create backup root {backup_root}: {error}") from error
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    for index in range(1000):
        suffix = "" if index == 0 else f"-{index}"
        directory = backup_root / f"backup-{timestamp}{suffix}"
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        except OSError as error:
            raise ConfigError(f"could not create backup directory {directory}: {error}") from error
        return directory
    raise ConfigError(f"could not allocate a unique backup directory in {backup_root}")


def _write_backup(path: Path, contents: bytes) -> None:
    try:
        with path.open("xb") as backup:
            backup.write(contents)
            backup.flush()
            os.fsync(backup.fileno())
    except OSError as error:
        raise ConfigError(f"could not write backup file {path}: {error}") from error


def _create_backup_from_sources(
    changes: list[Change], sources: list[_SourceFile], backup_root: Path
) -> Path:
    directory = _new_backup_dir(backup_root)
    files: list[dict[str, object]] = []
    for index, (change, source) in enumerate(zip(changes, sources)):
        backup_name = _backup_name(change.name, index)
        _write_backup(directory / backup_name, source.contents)
        files.append(
            {
                "name": change.name,
                "original_path": str(source.path),
                "backup_name": backup_name,
                "sha256": _sha256_bytes(source.contents),
                "mode": source.mode,
            }
        )

    manifest = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    try:
        (directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as error:
        raise ConfigError(f"could not write backup manifest in {directory}: {error}") from error
    return directory


def create_backup(changes: list[Change], backup_root: Path) -> Path:
    """Copy planned originals into a complete, timestamped backup directory."""
    if not changes:
        raise ConfigError("cannot create a backup without changes")
    return _create_backup_from_sources(changes, _source_files(changes), backup_root)


def _atomic_write(path: Path, contents: bytes, mode: int) -> None:
    """Replace a file with a fully flushed sibling temporary file."""
    if not path.parent.is_dir():
        raise ConfigError(f"configuration file parent directory does not exist: {path.parent}")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError as error:
        raise ConfigError(f"could not replace configuration file {path}: {error}") from error
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _attempt_rollbacks(rollbacks: list[Callable[[], None]]) -> list[ConfigError]:
    """Run every reverse-order rollback action, retaining all failures."""
    failures: list[ConfigError] = []
    for rollback in reversed(rollbacks):
        try:
            rollback()
        except ConfigError as error:
            failures.append(error)
    return failures


def _restore_backup_file(source: _SourceFile, backup: _BackupFile) -> None:
    _atomic_write(source.path, _read_backup_contents(backup), backup.mode)


def _rollback(replaced: list[_SourceFile], backups: dict[Path, _BackupFile]) -> list[ConfigError]:
    return _attempt_rollbacks(
        [partial(_restore_backup_file, source, backups[source.path]) for source in replaced]
    )


def apply_changes(changes: list[Change], backup_root: Path) -> Path | None:
    """Back up and atomically apply every planned change, rolling back on failure."""
    if not changes:
        return None
    sources = _source_files(changes)
    backup = _create_backup_from_sources(changes, sources, backup_root)
    backups = {file.original_path: file for file in _manifest_files(backup)}
    replaced: list[_SourceFile] = []
    try:
        for change, source in zip(changes, sources):
            _atomic_write(source.path, change.after.encode("utf-8"), source.mode)
            replaced.append(source)
    except (ConfigError, UnicodeError) as error:
        rollback_errors = _rollback(replaced, backups)
        if rollback_errors:
            raise ConfigError(
                "could not apply changes and rollback failed for "
                f"{len(rollback_errors)} file(s): {'; '.join(map(str, rollback_errors))}"
            ) from error
        raise ConfigError(f"could not apply changes; replaced files were rolled back: {error}") from error
    return backup


def _manifest_files(backup_dir: Path) -> list[_BackupFile]:
    """Load a complete backup manifest and verify every preserved byte."""
    directory = backup_dir.resolve()
    manifest_path = directory / "manifest.json"
    if not directory.is_dir() or not manifest_path.is_file():
        raise ConfigError(f"backup is incomplete: {backup_dir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"could not read backup manifest {manifest_path}: {error}") from error
    if not isinstance(manifest, dict):
        raise ConfigError(f"backup manifest must be an object: {manifest_path}")
    if manifest.get("version") != BACKUP_VERSION:
        raise ConfigError(f"unsupported backup manifest version in {manifest_path}")
    if not isinstance(manifest.get("created_at"), str):
        raise ConfigError(f"backup manifest has no creation time: {manifest_path}")
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ConfigError(f"backup manifest has no files: {manifest_path}")

    files: list[_BackupFile] = []
    originals: set[Path] = set()
    backup_names: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ConfigError(f"backup manifest has an invalid file record: {manifest_path}")
        required = {"name", "original_path", "backup_name", "sha256", "mode"}
        if not required.issubset(record):
            raise ConfigError(f"backup manifest has a missing file member: {manifest_path}")
        name = record["name"]
        original_value = record["original_path"]
        backup_name = record["backup_name"]
        expected_hash = record["sha256"]
        mode = record["mode"]
        if not isinstance(name, str) or not name:
            raise ConfigError(f"backup manifest has an invalid file name: {manifest_path}")
        if not isinstance(original_value, str) or not Path(original_value).is_absolute():
            raise ConfigError(f"backup manifest original path must be absolute: {manifest_path}")
        if (
            not isinstance(backup_name, str)
            or not backup_name
            or Path(backup_name).name != backup_name
        ):
            raise ConfigError(f"backup manifest has an unsafe backup name: {manifest_path}")
        if (
            not isinstance(expected_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        ):
            raise ConfigError(f"backup manifest has an invalid SHA-256 hash: {manifest_path}")
        if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= _MODE_MASK:
            raise ConfigError(f"backup manifest has an invalid mode: {manifest_path}")

        original_path = Path(original_value).resolve()
        backup_path = directory / backup_name
        if backup_path.resolve().parent != directory or not backup_path.is_file():
            raise ConfigError(f"backup manifest refers to a missing backup file: {backup_path}")
        if original_path in originals or backup_name in backup_names:
            raise ConfigError(f"backup manifest has duplicate file records: {manifest_path}")
        try:
            contents = backup_path.read_bytes()
        except OSError as error:
            raise ConfigError(f"could not read backup file {backup_path}: {error}") from error
        if _sha256_bytes(contents) != expected_hash:
            raise ConfigError(f"backup file hash does not match manifest: {backup_path}")
        originals.add(original_path)
        backup_names.add(backup_name)
        files.append(_BackupFile(name, original_path, backup_path, expected_hash, mode))
    return files


def latest_complete_backup(backup_root: Path) -> Path:
    """Return the newest backup directory whose manifest and files validate."""
    if not backup_root.is_dir():
        raise ConfigError(f"no complete backup exists in {backup_root}")
    try:
        candidates = [path for path in backup_root.iterdir() if path.is_dir()]
    except OSError as error:
        raise ConfigError(f"could not scan backup root {backup_root}: {error}") from error
    for candidate in sorted(
        candidates,
        key=_backup_sort_key,
        reverse=True,
    ):
        try:
            _manifest_files(candidate)
        except ConfigError:
            continue
        return candidate.resolve()
    raise ConfigError(f"no complete backup exists in {backup_root}")


def _backup_sort_key(path: Path) -> tuple[int, datetime, int, str]:
    match = _BACKUP_DIR_RE.fullmatch(path.name)
    if match is not None:
        try:
            timestamp = datetime.strptime(match["timestamp"], "%Y%m%dT%H%M%S.%fZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
        else:
            return (1, timestamp, int(match["suffix"] or 0), path.name)
    return (0, datetime.min.replace(tzinfo=timezone.utc), -1, path.name)


def _read_backup_contents(backup: _BackupFile) -> bytes:
    """Read exactly the bytes whose digest is recorded in a backup manifest."""
    try:
        contents = backup.backup_path.read_bytes()
    except OSError as error:
        raise ConfigError(f"could not read backup file {backup.backup_path}: {error}") from error
    if _sha256_bytes(contents) != backup.sha256:
        raise ConfigError(f"backup file hash does not match manifest: {backup.backup_path}")
    return contents


def _current_file(path: Path) -> _SourceFile | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise ConfigError(f"restore target is not a file: {path}")
    try:
        return _SourceFile(path, path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    except OSError as error:
        raise ConfigError(f"could not snapshot restore target {path}: {error}") from error


def _restore_previous_file(path: Path, previous: _SourceFile | None) -> None:
    if previous is None:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise ConfigError(f"could not remove newly restored file {path}: {error}") from error
    else:
        _atomic_write(path, previous.contents, previous.mode)


def _rollback_restore(replaced: list[tuple[Path, _SourceFile | None]]) -> list[ConfigError]:
    return _attempt_rollbacks(
        [partial(_restore_previous_file, path, previous) for path, previous in replaced]
    )


def restore_backup(backup_dir: Path) -> list[Path]:
    """Restore a validated complete backup with atomic sibling replacements."""
    files = _manifest_files(backup_dir)
    previous = [(file.original_path, _current_file(file.original_path)) for file in files]
    replaced: list[tuple[Path, _SourceFile | None]] = []
    try:
        for file, (_, old_file) in zip(files, previous):
            _atomic_write(file.original_path, _read_backup_contents(file), file.mode)
            replaced.append((file.original_path, old_file))
    except ConfigError as error:
        rollback_errors = _rollback_restore(replaced)
        if rollback_errors:
            raise ConfigError(
                "could not restore backup and rollback failed for "
                f"{len(rollback_errors)} file(s): {'; '.join(map(str, rollback_errors))}"
            ) from error
        raise ConfigError(f"could not restore backup; replaced files were rolled back: {error}") from error
    return [file.original_path for file in files]


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface for safe Darktide configuration changes."""
    parser = argparse.ArgumentParser(
        description="Safely preview, apply, or restore Darktide performance configuration fixes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  uv run python wh40k_darktide_fixes.py --dry-run
  uv run python wh40k_darktide_fixes.py --apply
  uv run python wh40k_darktide_fixes.py --restore""",
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the installation and print pending changes without writing files",
    )
    modes.add_argument(
        "--apply",
        action="store_true",
        help="back up and atomically apply validated pending changes",
    )
    modes.add_argument(
        "--restore",
        action="store_true",
        help="restore the latest complete backup without discovering an installation",
    )
    parser.add_argument("--game-dir", type=Path, help="override the Darktide game directory")
    parser.add_argument(
        "--compatdata-dir", type=Path, help="override Darktide's Steam compatdata directory"
    )
    parser.add_argument(
        "--home", type=Path, help="override the home directory used for Steam discovery"
    )
    return parser


def unified_diff(change: Change) -> str:
    """Return a standard unified diff for one planned file change."""
    return "".join(
        difflib.unified_diff(
            change.before.splitlines(keepends=True),
            change.after.splitlines(keepends=True),
            fromfile=str(change.path),
            tofile=str(change.path),
        )
    )


def _print_targets(targets: Targets) -> None:
    print("Managed configuration files:")
    for path in target_files(targets).values():
        print(f"  {path}")


def _discover_and_plan(args: argparse.Namespace) -> list[Change]:
    home = args.home if args.home is not None else Path.home()
    targets = discover_targets(home, args.game_dir, args.compatdata_dir)
    _print_targets(targets)
    return plan_changes(targets)


def _print_launch_option() -> None:
    print("Enter this Steam launch option manually:")
    print("%command% --lua-heap-mb-size 2048")


def _print_apply_warning() -> None:
    print("Warning: Steam updates or file verification may overwrite game-directory changes.")


def main(argv: list[str] | None = None) -> int:
    """Run the selected mode and return a conventional process exit status."""
    args = build_parser().parse_args(argv)
    try:
        if args.restore:
            backup = latest_complete_backup(BACKUP_ROOT)
            restored = restore_backup(backup)
            print(f"Restored {len(restored)} file(s) from backup: {backup}")
            return 0

        changes = _discover_and_plan(args)
        if args.dry_run:
            if not changes:
                print("No pending configuration changes.")
                return 0
            print("Pending configuration changes:")
            for change in changes:
                print(unified_diff(change), end="" if change.after.endswith("\n") else "\n")
            return 0

        if not changes:
            print("No pending configuration changes; managed files are already correct.")
            _print_launch_option()
            _print_apply_warning()
            return 0
        backup = apply_changes(changes, BACKUP_ROOT)
        print(f"Applied {len(changes)} file(s). Backup: {backup}")
        _print_launch_option()
        _print_apply_warning()
        return 0
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
