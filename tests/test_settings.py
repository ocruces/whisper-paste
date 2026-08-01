"""Tests for the persistent settings file (``whisper-paste.ini``).

Two things are being pinned here. First, that a hand-edited file can never stop
the app from starting: every malformed input degrades to a warning plus the
built-in default. Second — the reason this module exists at all — that the
precedence chain is *built-in defaults < ini < CLI flags*, in both directions:
a flag must beat the file, and the absence of a flag must not silently write
the flag's default over the file.

No file outside `tmp_path` is ever touched: the search-path helpers are
redirected by the autouse fixture, so a real `whisper-paste.ini` sitting in the
repository root or in `%LOCALAPPDATA%` cannot reach these tests.
"""

import os
import sys

import pytest

from whisper_paste import app
from whisper_paste import config
from whisper_paste import settings


_CONFIG_KEYS = (
    "USE_REFINER", "USE_GPU", "USE_CLIPBOARD", "WHISPER_LANGUAGE",
    "WHISPER_MODEL", "HOTKEY", "LOG_DIR", "LOG_TRANSCRIPTS",
)


@pytest.fixture(autouse=True)
def restore_config():
    saved = {key: getattr(config, key) for key in _CONFIG_KEYS}
    yield
    for key, value in saved.items():
        setattr(config, key, value)


@pytest.fixture(autouse=True)
def isolated_search_path(monkeypatch, tmp_path):
    """Point both search locations at empty temp directories.

    Without this the source-checkout branch of `_base_dir` resolves to the real
    repository root, so a settings file shipped with the project (or one the
    developer happens to have) would leak into every test.
    """
    app_dir = tmp_path / "appdir"
    local = tmp_path / "local"
    app_dir.mkdir()
    local.mkdir()
    monkeypatch.setattr(settings, "_base_dir", lambda: str(app_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return app_dir


def _freeze(monkeypatch, root):
    """Pose as a PyInstaller onedir bundle rooted at `root`.

    Same shape as the fixture in tests/test_bundle.py: exe at the ZIP root,
    PyInstaller's payload under `_internal`. A plain function rather than a
    fixture because the two callers must first undo `isolated_search_path`,
    which shares this test's monkeypatch instance.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(root / "_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(root / "WhisperPaste.exe"))
    return root


def _write(path, body):
    path.write_text(body, encoding="utf-8")
    return path


def _ini(tmp_path, body, name="whisper-paste.ini"):
    return _write(tmp_path / name, body)


def _load(path):
    return settings.parse_file(str(path))


# --------------------------------------------------------------------------- #
# Key -> config attribute mapping
# --------------------------------------------------------------------------- #
def test_every_documented_key_reaches_its_config_global(tmp_path):
    ini = _ini(tmp_path, (
        "[whisper-paste]\n"
        "language = es\n"
        "model = base\n"
        "hotkey = ctrl+alt+d\n"
        "refine = true\n"
        "output = type\n"
        "log_transcripts = yes\n"
        f"log_dir = {tmp_path}\n"
    ))

    result = settings.load_and_apply(str(ini))

    assert result.error is None
    assert result.warnings == []
    assert config.WHISPER_LANGUAGE == "es"
    assert config.WHISPER_MODEL == "base"
    assert config.HOTKEY == "ctrl+alt+d"
    assert config.USE_REFINER is True
    assert config.USE_CLIPBOARD is False
    assert config.LOG_TRANSCRIPTS is True
    assert config.LOG_DIR == str(tmp_path)


def test_output_type_disables_the_clipboard(tmp_path):
    settings.load_and_apply(str(_ini(tmp_path, "[whisper-paste]\noutput = type\n")))

    assert config.USE_CLIPBOARD is False


def test_output_clipboard_enables_the_clipboard(tmp_path):
    config.USE_CLIPBOARD = False

    settings.load_and_apply(str(_ini(tmp_path, "[whisper-paste]\noutput = clipboard\n")))

    assert config.USE_CLIPBOARD is True


def test_output_is_case_insensitive(tmp_path):
    settings.load_and_apply(str(_ini(tmp_path, "[whisper-paste]\noutput = TYPE\n")))

    assert config.USE_CLIPBOARD is False


def test_invalid_output_warns_and_keeps_the_default(tmp_path):
    result = settings.load_and_apply(
        str(_ini(tmp_path, "[whisper-paste]\noutput = paste\n"))
    )

    assert config.USE_CLIPBOARD is True
    assert result.error is None
    assert any("output" in w for w in result.warnings)


def test_only_the_keys_present_in_the_file_are_written(tmp_path):
    """The basis of precedence: absent keys must not be assigned at all."""
    result = _load(_ini(tmp_path, "[whisper-paste]\nlanguage = fr\n"))

    assert result.values == {"WHISPER_LANGUAGE": "fr"}


def test_keys_are_case_insensitive_and_accept_hyphens(tmp_path):
    result = settings.load_and_apply(
        str(_ini(tmp_path, "[whisper-paste]\nLog-Transcripts = true\n"))
    )

    assert result.warnings == []
    assert config.LOG_TRANSCRIPTS is True


# --------------------------------------------------------------------------- #
# Booleans
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, expected",
    [
        ("true", True), ("True", True), ("TRUE", True), ("yes", True),
        ("on", True), ("1", True), (" true ", True),
        ("false", False), ("False", False), ("no", False), ("off", False),
        ("0", False),
    ],
)
def test_boolean_spellings(tmp_path, text, expected):
    settings.load_and_apply(str(_ini(tmp_path, f"[whisper-paste]\nrefine = {text}\n")))

    assert config.USE_REFINER is expected


def test_invalid_boolean_warns_and_leaves_the_default(tmp_path):
    result = settings.load_and_apply(
        str(_ini(tmp_path, "[whisper-paste]\nrefine = maybe\n"))
    )

    assert config.USE_REFINER is False
    assert result.error is None
    assert any("refine" in w for w in result.warnings)


def test_empty_boolean_warns_and_leaves_the_default(tmp_path):
    result = settings.load_and_apply(str(_ini(tmp_path, "[whisper-paste]\nrefine =\n")))

    assert config.USE_REFINER is False
    assert any("refine" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# Language
# --------------------------------------------------------------------------- #
def test_empty_language_means_auto_detect_not_empty_string(tmp_path):
    """None, never "" — transcribe() passes the value straight to whisper."""
    config.WHISPER_LANGUAGE = "de"

    result = settings.load_and_apply(str(_ini(tmp_path, "[whisper-paste]\nlanguage =\n")))

    assert config.WHISPER_LANGUAGE is None
    assert result.warnings == []


def test_language_is_stripped(tmp_path):
    settings.load_and_apply(str(_ini(tmp_path, "[whisper-paste]\nlanguage =  es \n")))

    assert config.WHISPER_LANGUAGE == "es"


def test_empty_model_is_rejected_rather_than_applied(tmp_path):
    """An empty model name would break the loader; the default is better."""
    result = settings.load_and_apply(str(_ini(tmp_path, "[whisper-paste]\nmodel =\n")))

    assert config.WHISPER_MODEL == "small"
    assert any("model" in w for w in result.warnings)


def test_empty_log_dir_means_the_built_in_default(tmp_path):
    config.LOG_DIR = r"C:\somewhere"

    settings.load_and_apply(str(_ini(tmp_path, "[whisper-paste]\nlog_dir =\n")))

    assert config.LOG_DIR is None


# --------------------------------------------------------------------------- #
# Malformed input never stops the app
# --------------------------------------------------------------------------- #
def test_unknown_key_is_ignored_with_a_warning_and_valid_keys_still_apply(tmp_path):
    """A typo like `lang =` must say so, not silently do nothing."""
    result = settings.load_and_apply(
        str(_ini(tmp_path, "[whisper-paste]\nlang = es\nmodel = tiny\n"))
    )

    assert result.error is None
    assert config.WHISPER_MODEL == "tiny"
    assert config.WHISPER_LANGUAGE is None
    assert any("lang" in w for w in result.warnings)


def test_unknown_section_is_ignored_with_a_warning_and_valid_keys_still_apply(tmp_path):
    result = settings.load_and_apply(str(_ini(tmp_path, (
        "[whisperpaste]\n"
        "model = medium\n"
        "\n"
        "[whisper-paste]\n"
        "model = tiny\n"
    ))))

    assert result.error is None
    assert config.WHISPER_MODEL == "tiny"
    assert any("whisperpaste" in w for w in result.warnings)


def test_a_file_without_our_section_warns_and_changes_nothing(tmp_path):
    result = settings.load_and_apply(str(_ini(tmp_path, "[other]\nmodel = medium\n")))

    assert result.values == {}
    assert config.WHISPER_MODEL == "small"
    assert result.warnings


def test_a_file_that_is_not_valid_ini_does_not_raise(tmp_path):
    result = settings.load_and_apply(
        str(_ini(tmp_path, "this is not an ini file at all\n{ json: true }\n"))
    )

    assert result.error is not None
    assert result.values == {}
    assert config.WHISPER_MODEL == "small"


def test_a_duplicated_key_does_not_throw_the_file_away(tmp_path):
    result = settings.load_and_apply(
        str(_ini(tmp_path, "[whisper-paste]\nmodel = tiny\nmodel = base\n"))
    )

    assert result.error is None
    assert config.WHISPER_MODEL == "base"


def test_an_unreadable_file_is_reported_not_raised(tmp_path):
    """Opening a directory fails with OSError on every platform we run on."""
    result = settings.parse_file(str(tmp_path))

    assert result.error is not None
    assert result.values == {}


def test_a_file_in_the_wrong_encoding_still_yields_its_valid_keys(tmp_path):
    path = tmp_path / "whisper-paste.ini"
    path.write_bytes(
        "[whisper-paste]\n# caf\xe9 latin-1 comment\nmodel = tiny\n".encode("latin-1")
    )

    result = settings.load_and_apply(str(path))

    assert result.error is None
    assert config.WHISPER_MODEL == "tiny"
    assert any("UTF-8" in w for w in result.warnings)


def test_a_utf8_bom_does_not_break_the_first_section_header(tmp_path):
    path = tmp_path / "whisper-paste.ini"
    path.write_bytes("[whisper-paste]\nmodel = tiny\n".encode("utf-8-sig"))

    result = settings.load_and_apply(str(path))

    assert result.error is None
    assert result.warnings == []
    assert config.WHISPER_MODEL == "tiny"


def test_a_percent_sign_in_a_value_is_not_interpolated(tmp_path):
    r"""log_dir = %LOCALAPPDATA%\... must survive; interpolation would reject it."""
    result = settings.load_and_apply(str(_ini(
        tmp_path, "[whisper-paste]\nlog_dir = %LOCALAPPDATA%\\WhisperPaste\\logs\n"
    )))

    assert result.error is None
    assert config.LOG_DIR == r"%LOCALAPPDATA%\WhisperPaste\logs"


# --------------------------------------------------------------------------- #
# Locating the file
# --------------------------------------------------------------------------- #
def test_no_settings_file_anywhere_is_not_an_error():
    path, error = settings.find_config_file()

    assert path is None
    assert error is None


def test_load_with_no_file_leaves_config_untouched():
    result = settings.load_and_apply()

    assert result.path is None
    assert result.error is None
    assert result.values == {}
    assert config.WHISPER_MODEL == "small"


def test_a_missing_explicit_config_path_is_reported(tmp_path):
    """The user named that file; running on defaults instead must not be silent."""
    missing = tmp_path / "nope.ini"

    result = settings.load_and_apply(str(missing))

    assert result.error is not None
    assert "nope.ini" in result.error
    assert result.values == {}


def test_search_paths_are_the_exe_directory_then_localappdata_when_frozen(
    monkeypatch, tmp_path
):
    monkeypatch.undo()  # drop the isolated_search_path redirect of _base_dir
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    _freeze(monkeypatch, bundle_root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    first, second = settings.search_paths()

    assert first == os.path.join(str(bundle_root), "whisper-paste.ini")
    assert second == os.path.join(
        str(tmp_path / "local"), "WhisperPaste", "whisper-paste.ini"
    )


def test_the_exe_adjacent_file_wins_over_localappdata_when_frozen(monkeypatch, tmp_path):
    monkeypatch.undo()
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    _freeze(monkeypatch, bundle_root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    local_ini = tmp_path / "local" / "WhisperPaste" / "whisper-paste.ini"
    local_ini.parent.mkdir(parents=True)
    _write(local_ini, "[whisper-paste]\nmodel = from-localappdata\n")
    _ini(bundle_root, "[whisper-paste]\nmodel = beside-the-exe\n")

    settings.load_and_apply()

    assert config.WHISPER_MODEL == "beside-the-exe"


def test_search_path_from_source_is_the_repo_root(monkeypatch):
    """Beside the repo, not inside the package — an ini in whisper_paste/ reads as source."""
    monkeypatch.undo()
    monkeypatch.delattr(sys, "frozen", raising=False)

    expected = os.path.dirname(os.path.dirname(os.path.abspath(settings.__file__)))

    assert settings.search_paths()[0] == os.path.join(expected, "whisper-paste.ini")


def test_the_app_directory_file_beats_localappdata(isolated_search_path, tmp_path):
    local_ini = tmp_path / "local" / "WhisperPaste" / "whisper-paste.ini"
    local_ini.parent.mkdir(parents=True)
    _write(local_ini, "[whisper-paste]\nmodel = from-localappdata\n")
    _ini(isolated_search_path, "[whisper-paste]\nmodel = from-appdir\n")

    settings.load_and_apply()

    assert config.WHISPER_MODEL == "from-appdir"


def test_localappdata_is_used_when_nothing_sits_next_to_the_app(tmp_path):
    local_ini = tmp_path / "local" / "WhisperPaste" / "whisper-paste.ini"
    local_ini.parent.mkdir(parents=True)
    _write(local_ini, "[whisper-paste]\nmodel = from-localappdata\n")

    settings.load_and_apply()

    assert config.WHISPER_MODEL == "from-localappdata"


def test_an_explicit_path_beats_both_search_locations(isolated_search_path, tmp_path):
    local_ini = tmp_path / "local" / "WhisperPaste" / "whisper-paste.ini"
    local_ini.parent.mkdir(parents=True)
    _write(local_ini, "[whisper-paste]\nmodel = from-localappdata\n")
    _ini(isolated_search_path, "[whisper-paste]\nmodel = from-appdir\n")
    explicit = _ini(tmp_path, "[whisper-paste]\nmodel = from-explicit\n", "custom.ini")

    settings.load_and_apply(str(explicit))

    assert config.WHISPER_MODEL == "from-explicit"


def test_an_explicit_path_expands_environment_variables(tmp_path, monkeypatch):
    _ini(tmp_path, "[whisper-paste]\nmodel = from-env\n", "custom.ini")
    monkeypatch.setenv("WP_TEST_DIR", str(tmp_path))

    settings.load_and_apply("%WP_TEST_DIR%\\custom.ini")

    assert config.WHISPER_MODEL == "from-env"


# --------------------------------------------------------------------------- #
# Precedence: built-in defaults < ini < CLI
# --------------------------------------------------------------------------- #
def test_ini_refine_survives_a_command_line_that_does_not_mention_it(tmp_path):
    """The regression pin.

    `--refine` is store_true; when it defaulted to False, `_apply_args` wrote
    that False straight over the file's `refine = true` and the setting silently
    did nothing.
    """
    ini = _ini(tmp_path, "[whisper-paste]\nrefine = true\n")

    app._configure(["--config", str(ini)])

    assert config.USE_REFINER is True


def test_ini_output_type_survives_a_command_line_without_type(tmp_path):
    """Same bug, inverted flag: `USE_CLIPBOARD = not args.type` gave True."""
    ini = _ini(tmp_path, "[whisper-paste]\noutput = type\n")

    app._configure(["--config", str(ini)])

    assert config.USE_CLIPBOARD is False


def test_ini_log_transcripts_survives_a_bare_command_line(tmp_path):
    ini = _ini(tmp_path, "[whisper-paste]\nlog_transcripts = true\n")

    app._configure(["--config", str(ini)])

    assert config.LOG_TRANSCRIPTS is True


def test_a_cli_flag_beats_the_ini(tmp_path):
    ini = _ini(tmp_path, "[whisper-paste]\nlanguage = es\n")

    app._configure(["--config", str(ini), "--lang", "en"])

    assert config.WHISPER_LANGUAGE == "en"


def test_cli_type_beats_ini_output_clipboard(tmp_path):
    ini = _ini(tmp_path, "[whisper-paste]\noutput = clipboard\n")

    app._configure(["--config", str(ini), "--type"])

    assert config.USE_CLIPBOARD is False


def test_cli_model_and_log_dir_beat_the_ini(tmp_path):
    ini = _ini(tmp_path, (
        "[whisper-paste]\n"
        "model = base\n"
        "log_dir = C:\\from-ini\n"
    ))

    app._configure(["--config", str(ini), "--model", "tiny",
                    "--log-dir", "C:\\from-cli"])

    assert config.WHISPER_MODEL == "tiny"
    assert config.LOG_DIR == "C:\\from-cli"


def test_the_ini_beats_the_built_in_defaults(tmp_path):
    ini = _ini(tmp_path, "[whisper-paste]\nhotkey = ctrl+alt+d\nmodel = base\n")

    app._configure(["--config", str(ini)])

    assert config.HOTKEY == "ctrl+alt+d"
    assert config.WHISPER_MODEL == "base"


def test_cli_hotkey_beats_ini(tmp_path):
    """--hotkey must beat an ini that also set it."""
    ini = _ini(tmp_path, "[whisper-paste]\nhotkey = ctrl+alt+d\n")

    app._configure(["--config", str(ini), "--hotkey", "ctrl+shift+h"])

    assert config.HOTKEY == "ctrl+shift+h"


def test_ini_hotkey_applies_when_no_flag(tmp_path):
    """An ini hotkey must apply when --hotkey is not passed."""
    ini = _ini(tmp_path, "[whisper-paste]\nhotkey = ctrl+alt+d\n")

    app._configure(["--config", str(ini)])

    assert config.HOTKEY == "ctrl+alt+d"


def test_configure_without_any_settings_file_keeps_the_defaults():
    result = app._configure([])

    assert result.path is None
    assert config.USE_REFINER is False
    assert config.USE_CLIPBOARD is True
    assert config.WHISPER_MODEL == "small"


def test_configure_reports_a_missing_explicit_config_without_dying(tmp_path):
    result = app._configure(["--config", str(tmp_path / "nope.ini")])

    assert result.error is not None
    assert config.WHISPER_MODEL == "small"


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
@pytest.fixture
def boxes(monkeypatch):
    shown = []
    monkeypatch.setattr(app, "_message_box", lambda text, **kw: shown.append(str(text)))
    return shown


def test_a_load_error_reaches_a_dialog_in_a_frozen_build(monkeypatch, boxes):
    monkeypatch.setattr(app.bundle, "is_frozen", lambda: True)

    app._report_settings(settings.SettingsResult(error="boom"))

    assert boxes == ["boom"]


def test_a_load_error_is_log_only_from_source(monkeypatch, boxes):
    monkeypatch.setattr(app.bundle, "is_frozen", lambda: False)

    app._report_settings(settings.SettingsResult(error="boom"))

    assert boxes == []


def test_the_shipped_template_is_understood_by_this_reader():
    """Close the drift loop from the reader's side.

    tests/test_packaging.py pins the template against a hand-written key list;
    this pins the same file against the keys actually implemented here, so a key
    added to one and not the other cannot pass both. Skipped rather than failed
    if the template is not present, since it belongs to the packaging tree.
    """
    template = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "packaging", "whisper-paste.ini",
    )
    if not os.path.isfile(template):
        pytest.skip("packaging/whisper-paste.ini is not present")

    result = settings.parse_file(template)

    # Shipped fully commented out: it parses, finds our section, and sets nothing.
    assert result.error is None
    assert result.warnings == []
    assert result.values == {}

    documented = {
        line.lstrip(" \t;#").split("=", 1)[0].strip().lower()
        for line in open(template, encoding="utf-8")
        if line.lstrip().startswith((";", "#")) and "=" in line
        and line.lstrip(" \t;#").split("=", 1)[0].strip().isidentifier()
    }
    assert documented == set(settings._KEYS)


def test_per_key_warnings_never_raise_a_dialog(monkeypatch, boxes, caplog):
    """A mistyped key must not block startup behind a modal on every launch."""
    monkeypatch.setattr(app.bundle, "is_frozen", lambda: True)

    with caplog.at_level("WARNING"):
        app._report_settings(
            settings.SettingsResult(path="x.ini", warnings=["unknown key 'lang'"])
        )

    assert boxes == []
    assert "unknown key 'lang'" in caplog.text


def test_a_file_that_parsed_is_reported_as_loaded(caplog):
    """The path is worth an INFO line: three locations are searched."""
    with caplog.at_level("INFO"):
        app._report_settings(
            settings.SettingsResult(path="x.ini", values={"WHISPER_LANGUAGE": "es"})
        )

    assert "Settings loaded from x.ini" in caplog.text


def test_a_file_that_failed_to_parse_is_not_reported_as_loaded(caplog):
    """"Settings loaded from X" must never precede "could not parse X".

    `parse_file` stamps `path` on the result before it knows the file is
    usable, so both fields are set on a parse failure. Logged unconditionally,
    the INFO line claims the file took effect and the very next line says it was
    thrown away — which sends whoever is debugging a bad ini hunting for a
    second settings file that does not exist. The error message already names
    the path, so nothing is lost by staying quiet here.
    """
    with caplog.at_level("INFO"):
        app._report_settings(
            settings.SettingsResult(path="x.ini", error="Could not parse x.ini")
        )

    assert "Settings loaded from" not in caplog.text
    assert "Could not parse x.ini" in caplog.text
