"""Tests for CLI flag parsing and how it is written into `config`.

`config` is mutated at startup and every other module reads those attributes at
call time, so a flag that fails to land in `config` silently does nothing.
"""

import pytest

from whisper_paste import app
from whisper_paste import config


_CONFIG_KEYS = (
    "USE_REFINER", "USE_GPU", "USE_CLIPBOARD", "WHISPER_LANGUAGE",
    "WHISPER_MODEL", "LOG_DIR", "LOG_TRANSCRIPTS",
)


@pytest.fixture(autouse=True)
def restore_config():
    saved = {key: getattr(config, key) for key in _CONFIG_KEYS}
    yield
    for key, value in saved.items():
        setattr(config, key, value)


def _apply(argv):
    app._apply_args(app._build_parser().parse_args(argv))


def test_defaults_keep_transcripts_out_of_the_log_and_use_the_private_log_dir():
    """A bare command line leaves the built-in defaults in force.

    Since the settings file landed, `_apply_args` reaches these values by *not
    writing them* rather than by writing the flags' defaults — see
    `test_a_bare_parse_does_not_clobber_values_the_settings_file_set`, which
    pins the difference.
    """
    _apply([])

    assert config.LOG_TRANSCRIPTS is False
    assert config.LOG_DIR is None
    assert config.USE_CLIPBOARD is True
    assert config.USE_REFINER is False


def test_a_bare_parse_does_not_clobber_values_the_settings_file_set():
    """The precedence bug this whole flag/None dance exists to prevent.

    `main()` applies whisper-paste.ini and *then* the CLI. When --refine/--type/
    --log-transcripts defaulted to False, `_apply_args` wrote that False over
    whatever the file had just set, so every boolean in the file silently did
    nothing unless the user also passed the matching flag.
    """
    config.USE_REFINER = True
    config.USE_GPU = True
    config.USE_CLIPBOARD = False
    config.LOG_TRANSCRIPTS = True

    _apply([])

    assert config.USE_REFINER is True
    assert config.USE_GPU is True
    assert config.USE_CLIPBOARD is False
    assert config.LOG_TRANSCRIPTS is True


def test_store_true_flags_parse_as_none_when_absent():
    """None, not False: that is the only signal that a flag was not supplied."""
    args = app._build_parser().parse_args([])

    assert args.refine is None
    assert args.gpu is None
    assert args.type is None
    assert args.log_transcripts is None
    assert args.config is None


def test_flags_that_switch_something_off_still_reach_config():
    """--type must be able to beat an ini that asked for clipboard output."""
    config.USE_CLIPBOARD = True

    _apply(["--type"])

    assert config.USE_CLIPBOARD is False


def test_config_flag_carries_the_settings_file_path():
    args = app._build_parser().parse_args(["--config", r"C:\somewhere\x.ini"])

    assert args.config == r"C:\somewhere\x.ini"


def test_log_transcripts_flag_is_opt_in():
    _apply(["--log-transcripts"])

    assert config.LOG_TRANSCRIPTS is True


def test_log_dir_flag_overrides_the_default(tmp_path):
    _apply(["--log-dir", str(tmp_path)])

    assert config.LOG_DIR == str(tmp_path)


def test_existing_flags_still_reach_config():
    _apply(["--refine", "--gpu", "--type", "--lang", "es", "--model", "tiny"])

    assert config.USE_REFINER is True
    assert config.USE_GPU is True
    assert config.USE_CLIPBOARD is False
    assert config.WHISPER_LANGUAGE == "es"
    assert config.WHISPER_MODEL == "tiny"


def test_gpu_flag_still_reaches_config_when_frozen(monkeypatch):
    """Regression pin: the frozen-build rejection must NOT live in _apply_args.

    --gpu is unsupported in the portable build, but the rejection belongs in
    main() (so the user sees a dialog) and in transcriber.py (the authoritative
    check). Moving it here would look tidy and would silently kill the flag for
    everyone running from source.
    """
    monkeypatch.setattr(app.bundle, "is_frozen", lambda: True)

    _apply(["--gpu"])

    assert config.USE_GPU is True


def test_language_and_model_are_left_alone_when_not_passed():
    config.WHISPER_LANGUAGE = "fr"
    config.WHISPER_MODEL = "medium"

    _apply([])

    assert config.WHISPER_LANGUAGE == "fr"
    assert config.WHISPER_MODEL == "medium"
