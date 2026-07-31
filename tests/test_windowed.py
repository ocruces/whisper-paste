"""Tests for running with no console — PyInstaller --windowed / pythonw.exe.

In a windowed process ``sys.stdout`` and ``sys.stderr`` are both None. That is
not a hypothetical: ``scripts\\run.ps1 -NoConsole`` already launches
``pythonw.exe`` today. Anything that assumes a writable stream (argparse's
error output, a console control handler) silently fails or kills the process
with no message at all, so these pin the fallbacks.
"""

import pytest

from whisper_paste import app


@pytest.fixture
def boxes(monkeypatch):
    """Record every _message_box call instead of showing a modal dialog."""
    shown = []
    monkeypatch.setattr(app, "_message_box", lambda text, **kw: shown.append(str(text)))
    return shown


# --------------------------------------------------------------------------- #
# _parse_args
# --------------------------------------------------------------------------- #
def test_bad_flag_without_streams_shows_a_message_box(monkeypatch, boxes):
    """argparse writes to sys.stderr; on None that is an AttributeError.

    Without the StringIO stand-in the process dies on a typo with no output
    whatsoever — no console, no dialog, nothing.
    """
    monkeypatch.setattr(app.sys, "stdout", None)
    monkeypatch.setattr(app.sys, "stderr", None)

    with pytest.raises(SystemExit):
        app._parse_args(["--not-a-real-flag"])

    assert boxes, "no message box was shown"
    assert "usage" in boxes[0].lower()


def test_parse_args_restores_the_real_streams(monkeypatch, boxes):
    """The redirection is scoped to the call.

    A leaked StringIO would stay installed for the life of the process (growing
    without bound, since nothing drains it) and would poison every later test in
    this session.
    """
    monkeypatch.setattr(app.sys, "stdout", None)
    monkeypatch.setattr(app.sys, "stderr", None)

    with pytest.raises(SystemExit):
        app._parse_args(["--not-a-real-flag"])

    assert app.sys.stdout is None
    assert app.sys.stderr is None


def test_parse_args_restores_the_real_streams_on_success(monkeypatch):
    monkeypatch.setattr(app.sys, "stdout", None)
    monkeypatch.setattr(app.sys, "stderr", None)

    args = app._parse_args(["--refine"])

    assert args.refine is True
    assert app.sys.stdout is None
    assert app.sys.stderr is None


def test_parse_args_is_a_plain_parse_when_streams_exist(boxes):
    """With a console, behaviour is exactly _build_parser().parse_args()."""
    args = app._parse_args(["--gpu", "--lang", "es"])

    assert args.gpu is True
    assert args.lang == "es"
    assert boxes == []


def test_parse_args_with_streams_still_exits_on_a_bad_flag(boxes):
    with pytest.raises(SystemExit):
        app._parse_args(["--not-a-real-flag"])

    # No dialog needed — argparse already printed to the real stderr.
    assert boxes == []


# --------------------------------------------------------------------------- #
# _message_box
# --------------------------------------------------------------------------- #
def test_message_box_never_raises(monkeypatch):
    """Same discipline as _set_tray_title: every caller is on an error path.

    A failure here must not replace the error being reported or skip the
    sys.exit that follows it.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("MessageBox failed")

    monkeypatch.setattr(app.win32api, "MessageBox", boom)

    app._message_box("something went wrong")


def test_message_box_passes_the_text_through(monkeypatch):
    calls = []
    monkeypatch.setattr(app.win32api, "MessageBox",
                        lambda *args: calls.append(args) or 1)

    app._message_box("boom", title="Title")

    assert calls and calls[0][1] == "boom" and calls[0][2] == "Title"


# --------------------------------------------------------------------------- #
# _has_console
# --------------------------------------------------------------------------- #
def test_has_console_is_false_when_win32console_raises(monkeypatch):
    def boom():
        raise RuntimeError("no console")

    monkeypatch.setattr(app.win32console, "GetConsoleWindow", boom)

    assert app._has_console() is False


def test_has_console_is_false_without_a_console_window(monkeypatch):
    monkeypatch.setattr(app.win32console, "GetConsoleWindow", lambda: 0)

    assert app._has_console() is False


def test_has_console_is_true_with_a_console_window(monkeypatch):
    monkeypatch.setattr(app.win32console, "GetConsoleWindow", lambda: 0x1234)

    assert app._has_console() is True


# --------------------------------------------------------------------------- #
# Icon artwork
# --------------------------------------------------------------------------- #
def test_icon_at_the_default_size_is_unchanged():
    """size=64 must reproduce the original [8, 8, 56, 56] / width=2 drawing.

    The packaging script renders the same function at 256 for the exe's .ico;
    generalising the geometry must not have moved the tray icon by a pixel.
    """
    from PIL import Image, ImageDraw

    expected = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(expected).ellipse(
        [8, 8, 56, 56], fill=app.COLOR_IDLE, outline=(255, 255, 255, 200), width=2
    )

    assert app.create_icon_image(app.COLOR_IDLE).tobytes() == expected.tobytes()
    assert app.ICON_IDLE.tobytes() == expected.tobytes()


def test_icon_renders_at_the_ico_size():
    icon = app.create_icon_image(app.COLOR_IDLE, size=256)

    assert icon.size == (256, 256)
    assert icon.mode == "RGBA"
