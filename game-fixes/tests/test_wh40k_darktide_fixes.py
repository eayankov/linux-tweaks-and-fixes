from pathlib import Path
import stat

import pytest

import wh40k_darktide_fixes as fixes


def make_install(library: Path) -> tuple[Path, Path]:
    game = library / "steamapps/common" / fixes.GAME_DIR_NAME
    compat = library / "steamapps/compatdata" / fixes.APP_ID
    (game / "bundle/application_settings").mkdir(parents=True)
    (compat / "pfx/drive_c/users/steamuser/AppData/Roaming/Fatshark/Darktide").mkdir(parents=True)
    return game, compat


def test_parse_library_paths_supports_vdf_paths_and_escaped_backslashes():
    text = r'''"libraryfolders" { "0" { "path" "/home/me/.local/share/Steam" } "1" { "path" "D:\\SteamLibrary" } }'''
    assert fixes.parse_library_paths(text) == [
        Path("/home/me/.local/share/Steam"),
        Path(r"D:\SteamLibrary"),
    ]


def test_discover_targets_uses_libraryfolders(tmp_path: Path):
    home = tmp_path / "home"
    root = home / ".local/share/Steam"
    library = home / "games/SteamLibrary"
    (root / "steamapps").mkdir(parents=True)
    (root / "steamapps/libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "1" {{ "path" "{library}" }} }}', encoding="utf-8"
    )
    game, compat = make_install(library)
    assert fixes.discover_targets(home) == fixes.Targets(game.resolve(), compat.resolve())


def test_discovery_reports_libraryfolders_utf8_decode_error_with_path(tmp_path: Path):
    home = tmp_path / "home"
    vdf = home / ".local/share/Steam/steamapps/libraryfolders.vdf"
    vdf.parent.mkdir(parents=True)
    vdf.write_bytes(b"\xff")

    with pytest.raises(fixes.ConfigError, match=str(vdf)):
        fixes.discover_targets(home)


def test_discovery_reports_libraryfolders_read_error_with_path(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    vdf = home / ".local/share/Steam/steamapps/libraryfolders.vdf"
    vdf.parent.mkdir(parents=True)
    vdf.write_text('"libraryfolders" {}', encoding="utf-8")
    real_read_text = Path.read_text

    def fail_libraryfolders_read(path: Path, *args, **kwargs):
        if path == vdf:
            raise OSError("simulated libraryfolders read failure")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_libraryfolders_read)

    with pytest.raises(fixes.ConfigError, match=str(vdf)):
        fixes.discover_targets(home)


def test_explicit_overrides_can_use_different_libraries(tmp_path: Path):
    game, _ = make_install(tmp_path / "game-library")
    _, compat = make_install(tmp_path / "prefix-library")
    assert fixes.discover_targets(tmp_path, game, compat) == fixes.Targets(
        game.resolve(), compat.resolve()
    )


def test_automatic_discovery_rejects_game_and_prefix_in_different_libraries(tmp_path: Path):
    home = tmp_path / "home"
    game_library = home / "games/SteamLibrary"
    prefix_library = home / "prefixes/SteamLibrary"
    (game_library / "steamapps/common" / fixes.GAME_DIR_NAME).mkdir(parents=True)
    (prefix_library / "steamapps/compatdata" / fixes.APP_ID).mkdir(parents=True)

    with pytest.raises(fixes.ConfigError, match="same Steam library"):
        fixes.discover_targets(home)


def test_multiple_game_installs_are_rejected_as_ambiguous(tmp_path: Path):
    home = tmp_path / "home"
    first = home / "one/SteamLibrary"
    second = home / "two/SteamLibrary"
    make_install(first)
    make_install(second)
    with pytest.raises(fixes.ConfigError, match="multiple Darktide installations"):
        fixes.discover_targets(home)


def test_missing_install_is_reported(tmp_path: Path):
    with pytest.raises(fixes.ConfigError, match="Darktide installation was not found"):
        fixes.discover_targets(tmp_path)


def test_target_files_returns_the_exact_managed_paths(tmp_path: Path):
    targets = fixes.Targets(tmp_path / "game", tmp_path / "compatdata")

    assert fixes.target_files(targets) == {
        "win32_settings": tmp_path / "game/bundle/application_settings/win32_settings.ini",
        "settings_common": tmp_path / "game/bundle/application_settings/settings_common.ini",
        "user_settings": tmp_path
        / "compatdata/pfx/drive_c/users/steamuser/AppData/Roaming/Fatshark/Darktide/user_settings.config",
    }


WIN32 = """renderer = {\n  streaming_buffer_size = 64\n  streaming_texture_pool_size = 512\n  unrelated = 7\n}\n"""

COMMON = """feedback_streamer_settings = {\n  feedback_buffer_size = 4\n  max_age_out_tiles_per_frame = 64\n  max_streaming_tiles_per_frame = 64\n  max_texture_pool_size = 1024\n  max_write_feedback_threshold = 0.009\n  min_write_feedback_threshold = 0.005\n  staging_buffer_size = 4\n  threaded_streamer = true\n  tile_age_out_time_ms = 5000\n  tile_staging_buffer_size = 4\n}\nstreaming_buffer_size = 32\nstreaming_max_open_streams = 50\nstreaming_texture_pool_size = 400\ntexture_streamer_settings = {\n  streaming_buffer_size = 64\n  streaming_texture_pool_size = 512\n}\n"""

USER = """audio = { enabled = true }\nthreads = {\n  worker_threads = 11\n}\n"""


@pytest.fixture
def fake_install(tmp_path: Path):
    """Create the three managed configuration files with changeable settings."""
    targets = write_target_configs(tmp_path)
    originals = {
        path: path.read_text(encoding="utf-8")
        for path in fixes.target_files(targets).values()
    }
    return targets.game_dir, targets.compatdata_dir, originals


def test_dry_run_prints_diff_without_writing(fake_install, capsys):
    game, compat, originals = fake_install

    result = fixes.main(
        ["--dry-run", "--game-dir", str(game), "--compatdata-dir", str(compat)]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "---" in output and "+++" in output
    for path, content in originals.items():
        assert path.read_text(encoding="utf-8") == content


def test_successful_apply_prints_launch_option(fake_install, capsys, monkeypatch, tmp_path):
    game, compat, _ = fake_install
    monkeypatch.setattr(fixes, "BACKUP_ROOT", tmp_path / "backups")

    result = fixes.main(
        ["--apply", "--game-dir", str(game), "--compatdata-dir", str(compat)]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "%command% --lua-heap-mb-size 2048" in output
    assert "Steam updates or file verification may overwrite game-directory changes." in output


def test_already_correct_apply_prints_launch_option_without_creating_backup(
    fake_install, capsys, monkeypatch, tmp_path
):
    game, compat, _ = fake_install
    targets = fixes.Targets(game, compat)
    for name, path in fixes.target_files(targets).items():
        transform = {
            "win32_settings": fixes.transform_win32,
            "settings_common": fixes.transform_settings_common,
            "user_settings": fixes.transform_user_settings,
        }[name]
        path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")
    backup_root = tmp_path / "backups"
    monkeypatch.setattr(fixes, "BACKUP_ROOT", backup_root)

    result = fixes.main(
        ["--apply", "--game-dir", str(game), "--compatdata-dir", str(compat)]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "already correct" in output
    assert "%command% --lua-heap-mb-size 2048" in output
    assert "Steam updates or file verification may overwrite game-directory changes." in output
    assert not backup_root.exists()


def test_apply_reports_backup_root_creation_failure_without_traceback(
    fake_install, capsys, monkeypatch, tmp_path
):
    game, compat, _ = fake_install
    backup_root = tmp_path / "blocked-backups"
    real_mkdir = Path.mkdir

    def fail_backup_root_creation(path: Path, *args, **kwargs):
        if path == backup_root:
            raise OSError("simulated backup-root failure")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(fixes, "BACKUP_ROOT", backup_root)
    monkeypatch.setattr(Path, "mkdir", fail_backup_root_creation)

    result = fixes.main(
        ["--apply", "--game-dir", str(game), "--compatdata-dir", str(compat)]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.err == (
        f"error: could not create backup root {backup_root}: simulated backup-root failure\n"
    )
    assert "Traceback" not in captured.err


def test_apply_reports_backup_collision_directory_creation_failure_without_writes(
    fake_install, capsys, monkeypatch, tmp_path
):
    game, compat, originals = fake_install
    backup_root = tmp_path / "backups"
    real_mkdir = Path.mkdir

    def fail_collision_directory_creation(path: Path, *args, **kwargs):
        if path.parent == backup_root:
            raise OSError("simulated collision-directory failure")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(fixes, "BACKUP_ROOT", backup_root)
    monkeypatch.setattr(Path, "mkdir", fail_collision_directory_creation)

    result = fixes.main(
        ["--apply", "--game-dir", str(game), "--compatdata-dir", str(compat)]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "could not create backup directory" in captured.err
    assert str(backup_root) in captured.err
    assert "simulated collision-directory failure" in captured.err
    assert "Traceback" not in captured.err
    for path, original in originals.items():
        assert path.read_text(encoding="utf-8") == original


def test_restore_reports_backup_scan_failure_without_traceback(capsys, monkeypatch, tmp_path):
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    real_iterdir = Path.iterdir

    def fail_backup_scan(path: Path):
        if path == backup_root:
            raise OSError("simulated backup-scan failure")
        return real_iterdir(path)

    monkeypatch.setattr(fixes, "BACKUP_ROOT", backup_root)
    monkeypatch.setattr(Path, "iterdir", fail_backup_scan)

    result = fixes.main(["--restore"])

    captured = capsys.readouterr()
    assert result == 1
    assert captured.err == (
        f"error: could not scan backup root {backup_root}: simulated backup-scan failure\n"
    )
    assert "Traceback" not in captured.err


def test_failed_apply_does_not_print_launch_option(fake_install, capsys):
    game, compat, _ = fake_install
    (game / "bundle/application_settings/settings_common.ini").write_text(
        "malformed\n", encoding="utf-8"
    )

    assert (
        fixes.main(["--apply", "--game-dir", str(game), "--compatdata-dir", str(compat)])
        == 1
    )
    assert "%command% --lua-heap-mb-size 2048" not in capsys.readouterr().out


def test_parser_requires_exactly_one_mode():
    parser = fixes.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--dry-run", "--apply"])


def test_parser_help_includes_usage_examples():
    help_text = fixes.build_parser().format_help()

    assert "Examples:" in help_text
    assert "uv run python wh40k_darktide_fixes.py --dry-run" in help_text
    assert "uv run python wh40k_darktide_fixes.py --apply" in help_text
    assert "uv run python wh40k_darktide_fixes.py --restore" in help_text


def test_custom_tuning_options_control_every_managed_assignment():
    args = fixes.build_parser().parse_args(
        [
            "--dry-run",
            "--win32-streaming-buffer-size", "201",
            "--win32-streaming-texture-pool-size", "202",
            "--feedback-buffer-size", "203",
            "--max-age-out-tiles-per-frame", "204",
            "--max-streaming-tiles-per-frame", "205",
            "--max-texture-pool-size", "206",
            "--staging-buffer-size", "207",
            "--no-threaded-streamer",
            "--tile-age-out-time-ms", "208",
            "--tile-staging-buffer-size", "209",
            "--streaming-buffer-size", "210",
            "--streaming-max-open-streams", "211",
            "--streaming-texture-pool-size", "212",
            "--texture-streaming-buffer-size", "213",
            "--texture-streaming-texture-pool-size", "214",
            "--worker-threads", "215",
        ]
    )

    tuning = fixes.tuning_from_args(args)

    assert "streaming_buffer_size = 201" in fixes.transform_win32(WIN32, tuning)
    assert "streaming_texture_pool_size = 202" in fixes.transform_win32(WIN32, tuning)
    common = fixes.transform_settings_common(COMMON, tuning)
    for name, value in {
        "feedback_buffer_size": "203",
        "max_age_out_tiles_per_frame": "204",
        "max_streaming_tiles_per_frame": "205",
        "max_texture_pool_size": "206",
        "staging_buffer_size": "207",
        "threaded_streamer": "false",
        "tile_age_out_time_ms": "208",
        "tile_staging_buffer_size": "209",
        "streaming_max_open_streams": "211",
    }.items():
        assert f"{name} = {value}" in common
    assert "streaming_buffer_size = 210\n" in common
    assert "streaming_texture_pool_size = 212\n" in common
    assert "  streaming_buffer_size = 213\n" in common
    assert "  streaming_texture_pool_size = 214\n" in common
    assert "worker_threads = 215" in fixes.transform_user_settings(USER, tuning)


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_tuning_options_reject_non_positive_integers(value, capsys):
    with pytest.raises(SystemExit):
        fixes.build_parser().parse_args(["--dry-run", "--max-texture-pool-size", value])
    assert "must be a positive integer" in capsys.readouterr().err


def test_transform_win32_updates_streaming_values_and_preserves_other_lines():
    result = fixes.transform_win32(WIN32)
    assert "streaming_buffer_size = 128" in result
    assert "streaming_texture_pool_size = 1024" in result
    assert "unrelated = 7" in result


def test_transform_win32_supports_direct_assignments_inside_win32_block():
    real_layout = """win32 = {
  renderer = {
    ray_tracing = true
    screen_resolution = [ 1920 1080 ]
    streaming_buffer_size = 16
    streaming_texture_pool_size = 32
  }
  streaming_buffer_size = 64
  streaming_texture_pool_size = 512
}
"""

    result = fixes.transform_win32(real_layout)

    assert "  streaming_buffer_size = 128\n" in result
    assert "  streaming_texture_pool_size = 1024\n" in result
    assert "    ray_tracing = true\n" in result
    assert "    screen_resolution = [ 1920 1080 ]\n" in result
    assert "    streaming_buffer_size = 16\n" in result
    assert "    streaming_texture_pool_size = 32\n" in result


def test_transform_common_applies_exact_streamer_values_and_preserves_thresholds():
    result = fixes.transform_settings_common(COMMON)
    expected = {
        "feedback_buffer_size": "16",
        "max_age_out_tiles_per_frame": "16",
        "max_streaming_tiles_per_frame": "16",
        "max_texture_pool_size": "1024",
        "staging_buffer_size": "8",
        "threaded_streamer": "true",
        "tile_age_out_time_ms": "5000",
        "tile_staging_buffer_size": "64",
        "streaming_max_open_streams": "32",
    }
    for key, value in expected.items():
        assert f"{key} = {value}" in result
    assert result.count("streaming_buffer_size = 128") == 2
    assert result.count("streaming_texture_pool_size = 1024") >= 2
    assert "max_write_feedback_threshold = 0.009" in result
    assert "min_write_feedback_threshold = 0.005" in result


def test_transform_user_settings_sets_worker_threads_without_duplication():
    result = fixes.transform_user_settings(USER)
    assert result.count("worker_threads = 8") == 1
    assert "worker_threads = 11" not in result


@pytest.mark.parametrize(
    ("function", "text"),
    [
        (fixes.transform_win32, "unrelated = 1\n"),
        (fixes.transform_settings_common, "feedback_buffer_size = 4\n"),
        (fixes.transform_user_settings, "audio = true\n"),
    ],
)
def test_transform_rejects_unknown_layout(function, text):
    with pytest.raises(fixes.ConfigError, match="expected"):
        function(text)


def test_transformations_are_idempotent():
    for function, source in [
        (fixes.transform_win32, WIN32),
        (fixes.transform_settings_common, COMMON),
        (fixes.transform_user_settings, USER),
    ]:
        once = function(source)
        assert function(once) == once


NESTED_WIN32 = """renderer = {
  nested = {
    streaming_buffer_size = 64
    streaming_texture_pool_size = 512
  }
}
"""

NESTED_COMMON = """feedback_streamer_settings = {
  feedback_buffer_size = 4
  max_age_out_tiles_per_frame = 64
  max_streaming_tiles_per_frame = 64
  max_texture_pool_size = 1024
  staging_buffer_size = 4
  threaded_streamer = true
  tile_age_out_time_ms = 5000
  tile_staging_buffer_size = 4
}
streaming_buffer_size = 32
streaming_max_open_streams = 50
streaming_texture_pool_size = 400
texture_streamer_settings = {
  nested = {
    streaming_buffer_size = 64
    streaming_texture_pool_size = 512
  }
}
"""

NESTED_USER = """threads = {
  nested = {
    worker_threads = 11
  }
}
"""


@pytest.mark.parametrize(
    ("function", "text"),
    [
        (fixes.transform_win32, NESTED_WIN32),
        (fixes.transform_settings_common, NESTED_COMMON),
        (fixes.transform_user_settings, NESTED_USER),
    ],
)
def test_transform_rejects_settings_present_only_in_nested_blocks(function, text):
    with pytest.raises(fixes.ConfigError, match="expected"):
        function(text)


def test_transform_updates_direct_settings_without_changing_nested_settings():
    win32 = WIN32.replace(
        "  unrelated = 7\n", "  nested = {\n    streaming_buffer_size = 64\n  }\n"
    )
    common = COMMON.replace(
        "  streaming_texture_pool_size = 512\n}",
        "  streaming_texture_pool_size = 512\n  nested = {\n    streaming_buffer_size = 64\n  }\n}",
    )
    user = USER.replace(
        "  worker_threads = 11\n", "  worker_threads = 11\n  nested = {\n    worker_threads = 11\n  }\n"
    )

    assert "    streaming_buffer_size = 64" in fixes.transform_win32(win32)
    assert "    streaming_buffer_size = 64" in fixes.transform_settings_common(common)
    assert "    worker_threads = 11" in fixes.transform_user_settings(user)


@pytest.mark.parametrize(
    ("function", "text"),
    [
        (fixes.transform_win32, WIN32.replace("  unrelated = 7\n", "  streaming_buffer_size = 96\n")),
        (
            fixes.transform_settings_common,
            COMMON.replace("streaming_max_open_streams = 50\n", "streaming_max_open_streams = 50\nstreaming_max_open_streams = 51\n"),
        ),
        (
            fixes.transform_user_settings,
            USER.replace(
                "  worker_threads = 11\n}\n", "  worker_threads = 11\n  worker_threads = 12\n}\n"
            ),
        ),
    ],
)
def test_transform_rejects_duplicate_direct_assignments(function, text):
    with pytest.raises(fixes.ConfigError, match="expected"):
        function(text)


def test_transform_preserves_crlf_line_endings_and_indentation():
    result = fixes.transform_win32(WIN32.replace("  ", "\t").replace("\n", "\r\n"))
    assert "\tstreaming_buffer_size = 128\r\n" in result
    assert "\tstreaming_texture_pool_size = 1024\r\n" in result


def write_target_configs(tmp_path: Path, win32: str = WIN32, common: str = COMMON, user: str = USER):
    game, compat = make_install(tmp_path / "library")
    targets = fixes.Targets(game, compat)
    for name, text in {
        "win32_settings": win32,
        "settings_common": common,
        "user_settings": user,
    }.items():
        fixes.target_files(targets)[name].write_text(text, encoding="utf-8")
    return targets


def test_plan_changes_returns_all_valid_changed_files(tmp_path: Path):
    targets = write_target_configs(tmp_path)

    changes = fixes.plan_changes(targets)

    assert [(change.name, change.path) for change in changes] == list(fixes.target_files(targets).items())
    assert all(change.before != change.after for change in changes)


def test_plan_changes_omits_unchanged_files(tmp_path: Path):
    targets = write_target_configs(
        tmp_path,
        fixes.transform_win32(WIN32),
        fixes.transform_settings_common(COMMON),
        fixes.transform_user_settings(USER),
    )

    assert fixes.plan_changes(targets) == []


@pytest.mark.parametrize("make_non_file", [False, True])
def test_plan_changes_rejects_missing_and_non_file_paths(tmp_path: Path, make_non_file: bool):
    targets = write_target_configs(tmp_path)
    path = fixes.target_files(targets)["win32_settings"]
    if make_non_file:
        path.unlink()
        path.mkdir()
    else:
        path.unlink()

    with pytest.raises(fixes.ConfigError, match=str(path)):
        fixes.plan_changes(targets)


def test_plan_changes_reports_path_for_utf8_decode_errors(tmp_path: Path):
    targets = write_target_configs(tmp_path)
    path = fixes.target_files(targets)["settings_common"]
    path.write_bytes(b"\xff")

    with pytest.raises(fixes.ConfigError, match=str(path)):
        fixes.plan_changes(targets)


def test_plan_and_apply_preserve_crlf_bytes_without_false_change_detection(tmp_path: Path):
    targets = write_target_configs(tmp_path)
    original_bytes = {}
    for name, text in {
        "win32_settings": WIN32,
        "settings_common": COMMON,
        "user_settings": USER,
    }.items():
        path = fixes.target_files(targets)[name]
        original_bytes[path] = text.replace("\n", "\r\n").encode("utf-8")
        path.write_bytes(original_bytes[path])

    changes = fixes.plan_changes(targets)
    backup = fixes.apply_changes(changes, tmp_path / "backups")

    assert backup is not None
    assert len(changes) == 3
    manifest = fixes.json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    records = {Path(record["original_path"]): record for record in manifest["files"]}
    for change in changes:
        assert "\r\n" in change.before
        assert change.path.read_bytes() == change.after.encode("utf-8")
        assert (backup / records[change.path.resolve()]["backup_name"]).read_bytes() == original_bytes[
            change.path
        ]


def test_apply_creates_manifest_and_preserves_mode(tmp_path: Path):
    target = tmp_path / "target.ini"
    target.write_text("before\n", encoding="utf-8")
    target.chmod(0o640)
    change = fixes.Change("target", target, "before\n", "after\n")

    backup = fixes.apply_changes([change], tmp_path / "backups")

    assert backup is not None
    assert target.read_text(encoding="utf-8") == "after\n"
    assert target.stat().st_mode & 0o777 == 0o640
    manifest = fixes.json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"][0]["original_path"] == str(target.resolve())
    assert manifest["files"][0]["sha256"] == fixes.sha256_text("before\n")


def test_empty_apply_does_not_create_backup(tmp_path: Path):
    assert fixes.apply_changes([], tmp_path / "backups") is None
    assert not (tmp_path / "backups").exists()


def test_apply_rolls_back_replaced_files_when_later_replace_fails(tmp_path: Path, monkeypatch):
    first = tmp_path / "first.ini"
    second = tmp_path / "second.ini"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    changes = [
        fixes.Change("first", first, "one\n", "ONE\n"),
        fixes.Change("second", second, "two\n", "TWO\n"),
    ]
    real_replace = fixes.os.replace
    calls = 0

    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(fixes.os, "replace", fail_second)

    with pytest.raises(fixes.ConfigError, match="rolled back"):
        fixes.apply_changes(changes, tmp_path / "backups")

    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"


def test_restore_uses_latest_complete_backup(tmp_path: Path):
    target = tmp_path / "target.ini"
    target.write_text("before\n", encoding="utf-8")
    backup = fixes.apply_changes(
        [fixes.Change("target", target, "before\n", "after\n")],
        tmp_path / "backups",
    )
    target.write_text("later\n", encoding="utf-8")

    restored = fixes.restore_backup(fixes.latest_complete_backup(tmp_path / "backups"))

    assert restored == [target.resolve()]
    assert target.read_text(encoding="utf-8") == "before\n"
    assert backup.exists()


def test_restore_rejects_backup_with_mismatched_raw_bytes_hash(tmp_path: Path):
    target = tmp_path / "target.ini"
    target.write_text("before\n", encoding="utf-8")
    backup = fixes.apply_changes(
        [fixes.Change("target", target, "before\n", "after\n")],
        tmp_path / "backups",
    )
    (backup / "target-000.bak").write_bytes(b"corrupted\n")

    with pytest.raises(fixes.ConfigError, match="hash"):
        fixes.restore_backup(backup)

    assert target.read_text(encoding="utf-8") == "after\n"


def test_restore_does_not_use_backup_bytes_changed_after_validation(tmp_path: Path, monkeypatch):
    target = tmp_path / "target.ini"
    target.write_text("before\n", encoding="utf-8")
    backup = fixes.apply_changes(
        [fixes.Change("target", target, "before\n", "after\n")],
        tmp_path / "backups",
    )
    backup_file = backup / "target-000.bak"
    real_read_bytes = Path.read_bytes
    backup_reads = 0

    def mutate_after_first_backup_read(path: Path):
        nonlocal backup_reads
        contents = real_read_bytes(path)
        if path == backup_file:
            backup_reads += 1
            if backup_reads == 1:
                backup_file.write_bytes(b"tampered\n")
        return contents

    monkeypatch.setattr(Path, "read_bytes", mutate_after_first_backup_read)

    with pytest.raises(fixes.ConfigError, match="hash"):
        fixes.restore_backup(backup)

    assert target.read_text(encoding="utf-8") == "after\n"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("version", 2, "version"),
        ("original_path", "relative.ini", "absolute"),
    ],
)
def test_restore_rejects_unsupported_or_unsafe_manifest(tmp_path: Path, field, value, message):
    target = tmp_path / "target.ini"
    target.write_text("before\n", encoding="utf-8")
    backup = fixes.apply_changes(
        [fixes.Change("target", target, "before\n", "after\n")],
        tmp_path / "backups",
    )
    manifest_path = backup / "manifest.json"
    manifest = fixes.json.loads(manifest_path.read_text(encoding="utf-8"))
    if field == "version":
        manifest[field] = value
    else:
        manifest["files"][0][field] = value
    manifest_path.write_text(fixes.json.dumps(manifest), encoding="utf-8")

    with pytest.raises(fixes.ConfigError, match=message):
        fixes.restore_backup(backup)


def test_apply_attempts_every_rollback_when_one_rollback_replacement_fails(tmp_path: Path, monkeypatch):
    first = tmp_path / "first.ini"
    second = tmp_path / "second.ini"
    third = tmp_path / "third.ini"
    for path, contents in [(first, "one\n"), (second, "two\n"), (third, "three\n")]:
        path.write_text(contents, encoding="utf-8")
    changes = [
        fixes.Change("first", first, "one\n", "ONE\n"),
        fixes.Change("second", second, "two\n", "TWO\n"),
        fixes.Change("third", third, "three\n", "THREE\n"),
    ]
    real_replace = fixes.os.replace
    calls = 0

    def fail_apply_and_one_rollback(source, destination):
        nonlocal calls
        calls += 1
        if calls in {3, 4}:
            raise OSError("simulated replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(fixes.os, "replace", fail_apply_and_one_rollback)

    with pytest.raises(fixes.ConfigError, match="rollback failed"):
        fixes.apply_changes(changes, tmp_path / "backups")

    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "TWO\n"
    assert third.read_text(encoding="utf-8") == "three\n"


def test_restore_attempts_every_rollback_when_one_rollback_replacement_fails(tmp_path: Path, monkeypatch):
    first = tmp_path / "first.ini"
    second = tmp_path / "second.ini"
    third = tmp_path / "third.ini"
    for path, contents in [(first, "one\n"), (second, "two\n"), (third, "three\n")]:
        path.write_text(contents, encoding="utf-8")
    backup = fixes.apply_changes(
        [
            fixes.Change("first", first, "one\n", "ONE\n"),
            fixes.Change("second", second, "two\n", "TWO\n"),
            fixes.Change("third", third, "three\n", "THREE\n"),
        ],
        tmp_path / "backups",
    )
    real_replace = fixes.os.replace
    calls = 0

    def fail_restore_and_one_rollback(source, destination):
        nonlocal calls
        calls += 1
        if calls in {3, 4}:
            raise OSError("simulated replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(fixes.os, "replace", fail_restore_and_one_rollback)

    with pytest.raises(fixes.ConfigError, match="rollback failed"):
        fixes.restore_backup(backup)

    assert first.read_text(encoding="utf-8") == "ONE\n"
    assert second.read_text(encoding="utf-8") == "two\n"
    assert third.read_text(encoding="utf-8") == "THREE\n"


def test_latest_complete_backup_orders_numeric_collision_suffixes(tmp_path: Path):
    target = tmp_path / "target.ini"
    target.write_text("before\n", encoding="utf-8")
    root = tmp_path / "backups"
    ninth = fixes.apply_changes(
        [fixes.Change("target", target, "before\n", "after\n")], root
    )
    ninth = ninth.rename(root / "backup-20260807T000000.000000Z-9")
    tenth = fixes.apply_changes(
        [fixes.Change("target", target, "after\n", "later\n")], root
    )
    tenth = tenth.rename(root / "backup-20260807T000000.000000Z-10")

    assert fixes.latest_complete_backup(root) == tenth.resolve()
    assert ninth.exists()


def test_apply_preserves_supported_special_mode_bits(tmp_path: Path):
    target = tmp_path / "target.ini"
    target.write_text("before\n", encoding="utf-8")
    expected_mode = stat.S_ISUID | 0o640
    target.chmod(expected_mode)

    backup = fixes.apply_changes(
        [fixes.Change("target", target, "before\n", "after\n")], tmp_path / "backups"
    )

    assert stat.S_IMODE(target.stat().st_mode) == expected_mode
    manifest = fixes.json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"][0]["mode"] == expected_mode
