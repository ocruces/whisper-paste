"""Tests for log placement and for keeping transcripts out of the log.

The log is a durable plaintext artifact: everything dictated would otherwise be
persisted forever, and its file permissions would depend on wherever the repo
happened to be cloned. These tests pin both behaviours.
"""

import logging
import os

import pytest

from whisper_paste import app
from whisper_paste import config


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class _FakeRecorder:
    is_recording = False

    def stop(self):
        return "AUDIO"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    @property
    def messages(self):
        return [r.getMessage() for r in self.records]


@pytest.fixture(autouse=True)
def restore_logging():
    """_setup_logging() rewires the root logger; put it back afterwards."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_root_level = root.level
    app_logger = logging.getLogger("whisper-paste")
    saved_app_level = app_logger.level
    saved_log_dir = config.LOG_DIR
    saved_log_transcripts = config.LOG_TRANSCRIPTS
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_root_level)
    app_logger.setLevel(saved_app_level)
    config.LOG_DIR = saved_log_dir
    config.LOG_TRANSCRIPTS = saved_log_transcripts


def _run_pipeline(monkeypatch, transcript, level):
    """Run process_recording with fake collaborators; return the log messages."""
    monkeypatch.setattr(app, "recorder", _FakeRecorder())
    monkeypatch.setattr(app, "transcribe", lambda audio: transcript)
    monkeypatch.setattr(app, "paste_text", lambda text: None)
    monkeypatch.setattr(app, "update_tray", lambda state, tooltip=None: None)
    monkeypatch.setattr(config, "USE_REFINER", False)

    logger = logging.getLogger("whisper-paste")
    handler = _Capture()
    logger.setLevel(level)
    logger.addHandler(handler)
    try:
        app.process_recording()
    finally:
        logger.removeHandler(handler)
    return handler.messages


# --------------------------------------------------------------------------- #
# Where the log lives
# --------------------------------------------------------------------------- #
def test_log_dir_defaults_to_a_private_per_user_directory(monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", None)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")

    resolved = app._resolve_log_dir()

    assert resolved == os.path.join(
        r"C:\Users\someone\AppData\Local", "WhisperPaste", "logs"
    )


def test_log_dir_is_not_inside_the_repository_by_default(monkeypatch):
    """The whole point: the log must not inherit the clone location's ACL."""
    monkeypatch.setattr(config, "LOG_DIR", None)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(app.__file__)))

    assert not app._resolve_log_dir().startswith(repo_root)


def test_configured_log_dir_overrides_the_default(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))

    assert app._resolve_log_dir() == str(tmp_path)


def test_log_dir_falls_back_to_home_when_localappdata_is_absent(monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert app._resolve_log_dir().startswith(os.path.expanduser("~"))


# --------------------------------------------------------------------------- #
# What goes into it
# --------------------------------------------------------------------------- #
def test_transcript_text_is_not_logged_by_default(monkeypatch):
    secret = "my banking password is hunter two"

    messages = _run_pipeline(monkeypatch, secret, level=logging.INFO)

    assert not any(secret in m for m in messages)
    # Metadata is still logged so the pipeline stays debuggable.
    assert any("chars" in m for m in messages)


def test_transcript_text_is_logged_when_explicitly_enabled(monkeypatch):
    secret = "my banking password is hunter two"

    messages = _run_pipeline(monkeypatch, secret, level=logging.DEBUG)

    assert any(secret in m for m in messages)


def test_setup_logging_enables_transcript_debug_only_when_configured(monkeypatch, tmp_path):
    app_logger = logging.getLogger("whisper-paste")

    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "LOG_TRANSCRIPTS", False)
    app._setup_logging()
    assert app_logger.isEnabledFor(logging.DEBUG) is False

    monkeypatch.setattr(config, "LOG_TRANSCRIPTS", True)
    app._setup_logging()
    assert app_logger.isEnabledFor(logging.DEBUG) is True


def test_setup_logging_writes_into_the_resolved_directory(monkeypatch, tmp_path):
    log_dir = tmp_path / "nested"
    monkeypatch.setattr(config, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(config, "LOG_TRANSCRIPTS", False)

    app._setup_logging()
    logging.getLogger("whisper-paste").info("hello")

    assert (log_dir / "whisper-paste.log").exists()
