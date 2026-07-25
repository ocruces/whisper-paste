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
    _apply([])

    assert config.LOG_TRANSCRIPTS is False
    assert config.LOG_DIR is None
    assert config.USE_CLIPBOARD is True
    assert config.USE_REFINER is False


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


def test_language_and_model_are_left_alone_when_not_passed():
    config.WHISPER_LANGUAGE = "fr"
    config.WHISPER_MODEL = "medium"

    _apply([])

    assert config.WHISPER_LANGUAGE == "fr"
    assert config.WHISPER_MODEL == "medium"
