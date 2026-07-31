"""Tests for frozen-bundle awareness — `sys` is monkeypatched to fake PyInstaller."""

import os
import sys

import pytest

from whisper_paste import bundle


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pose as a PyInstaller onedir bundle rooted at tmp_path.

    Mirrors the shipped layout: the exe at the ZIP root, PyInstaller's own
    payload under `_internal`, and models beside the exe in `models\\<name>`.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "WhisperPaste.exe"))
    return tmp_path


def _make_model_dir(root, name):
    path = root / "models" / name
    path.mkdir(parents=True)
    return path


def test_not_frozen_in_a_source_checkout():
    """The test suite itself runs from source, so both markers must be absent."""
    assert bundle.is_frozen() is False


def test_frozen_flag_alone_is_not_enough(monkeypatch):
    """sys.frozen without _MEIPASS says nothing about the bundle layout."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    assert bundle.is_frozen() is False


def test_frozen_needs_both_markers(frozen):
    assert bundle.is_frozen() is True


def test_bundled_model_dir_is_none_when_not_frozen(monkeypatch, tmp_path):
    """A source checkout keeps today's behaviour even if models/ happens to exist."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    _make_model_dir(tmp_path, "small")

    assert bundle.bundled_model_dir("small") is None


def test_bundled_model_dir_returns_shipped_model(frozen):
    expected = _make_model_dir(frozen, "small")

    result = bundle.bundled_model_dir("small")

    assert result is not None
    assert os.path.normcase(result) == os.path.normcase(str(expected))
    assert os.path.isabs(result)


def test_bundled_model_dir_is_none_for_a_model_that_did_not_ship(frozen):
    """`--model medium` on a ZIP built with `small` must download as usual.

    Returning anything but None here would hand WhisperModel a non-existent
    directory instead of letting it resolve the name via HuggingFace.
    """
    _make_model_dir(frozen, "small")

    assert bundle.bundled_model_dir("medium") is None


def test_bundled_model_dir_is_none_when_nothing_shipped(frozen):
    assert bundle.bundled_model_dir("small") is None


@pytest.mark.parametrize(
    "name",
    [
        "Systran/faster-whisper-small",  # HuggingFace repo id
        r"C:\models\x",                  # absolute path
        "a/b",
        r"a\b",
        "..",
        ".",
        "",
        None,
    ],
)
def test_non_bare_names_pass_through_untouched(frozen, name):
    """Anything that is not a bare model name must reach WhisperModel unchanged.

    Creating the directory the naive join would point at proves the None comes
    from the name check, not from the path simply not existing — which is also
    what stops `--model ..\\..\\x` from aiming the lookup outside the bundle.
    """
    if name and not os.path.isabs(name):
        os.makedirs(os.path.join(str(frozen), "models", name), exist_ok=True)

    assert bundle.bundled_model_dir(name) is None


def test_gpu_message_points_at_the_readme():
    """Keep the message actionable: it is all the user sees in the tray tooltip."""
    message = bundle.GPU_UNSUPPORTED_MESSAGE

    assert "--gpu" in message
    assert "README" in message
    assert "https://github.com/ocruces/whisper-paste#gpu-support-amd--non-nvidia" in message
