"""Persistent settings file (``whisper-paste.ini``).

Why this exists: the portable ZIP is launched by double-clicking an exe, and a
double-click cannot pass command-line flags. Everything that used to be
CLI-only therefore needs a durable home, at minimum the transcription language.

Two rules shape the whole module.

**Precedence is built-in defaults < ini < CLI flags.** This module only ever
writes the keys a file actually contained (`parse_file` returns exactly those,
nothing more), so `app._apply_args` running afterwards can overwrite an ini
value with a flag while leaving untouched every value the user did not pass.
The mirror-image rule lives in `_build_parser`: every `store_true` flag
defaults to None so "not supplied" is distinguishable from "supplied as False".

**Nothing here may stop the app from starting.** It runs before the tray exists
in a process that usually has no console, so a syntax error, a mistyped key, a
bad boolean, an unreadable file or a file saved in the wrong encoding all
degrade to a warning plus the built-in default. Every problem is collected into
the returned `SettingsResult` instead of being logged here, because logging is
not configured yet at the point `main()` calls us — `--log-dir` and
`--log-transcripts` are themselves settings this module can supply.

`config` is imported as a *module reference* and written through `setattr`, per
CLAUDE.md ("Config module is mutated at startup"): value-importing the globals
here would bind them before the CLI ever ran.
"""

import configparser
import dataclasses
import logging
import os
import sys

from whisper_paste import bundle
from whisper_paste import config

logger = logging.getLogger("whisper-paste")

# Fixed contract — the shipped template file is generated against these.
CONFIG_FILENAME = "whisper-paste.ini"
SECTION = "whisper-paste"

# configparser's own boolean vocabulary: true/false, yes/no, on/off, 1/0.
# Reused rather than re-invented so the file behaves the way an ini is expected
# to, and so this stays in step if the stdlib ever widens it.
_BOOLEAN_STATES = configparser.ConfigParser.BOOLEAN_STATES


class _Invalid(ValueError):
    """A value that could not be understood. The message reaches the user."""


def _parse_bool(value):
    text = value.strip().lower()
    if text in _BOOLEAN_STATES:
        return _BOOLEAN_STATES[text]
    raise _Invalid("expected one of true/false, yes/no, on/off, 1/0")


def _parse_language(value):
    # Empty is meaningful and is the documented way to ask for auto-detection;
    # config.WHISPER_LANGUAGE is None for that, never "" (transcriber passes the
    # value straight to whisper, where "" is not a language code).
    text = value.strip()
    return text or None


def _parse_output(value):
    text = value.strip().lower()
    if text == "clipboard":
        return True
    if text == "type":
        return False
    raise _Invalid("expected 'clipboard' or 'type'")


def _parse_required_text(value):
    text = value.strip()
    if not text:
        raise _Invalid("must not be empty")
    return text


def _parse_path(value):
    # Left unexpanded: app._resolve_log_dir already does expandvars/expanduser/
    # abspath, and doing it twice would mangle a literal '%' in a directory
    # name. Empty means "use the built-in default", i.e. None.
    text = value.strip()
    return text or None


# ini key -> (config attribute, value parser)
_KEYS = {
    "language": ("WHISPER_LANGUAGE", _parse_language),
    "model": ("WHISPER_MODEL", _parse_required_text),
    "hotkey": ("HOTKEY", _parse_required_text),
    "refine": ("USE_REFINER", _parse_bool),
    "output": ("USE_CLIPBOARD", _parse_output),
    "log_transcripts": ("LOG_TRANSCRIPTS", _parse_bool),
    "log_dir": ("LOG_DIR", _parse_path),
}


@dataclasses.dataclass
class SettingsResult:
    """What happened while loading the settings file.

    `path` is the file that was read (None when no file was found, which is the
    normal case and not a problem). `values` maps config attribute -> value and
    contains *only* keys the file actually set. `warnings` are per-key problems
    the user should fix but that did not stop the load. `error` is set when the
    whole file was skipped — a missing explicit `--config`, an unreadable file,
    or one that does not parse as INI at all — and is the only thing worth a
    modal dialog.
    """

    path: str = None
    values: dict = dataclasses.field(default_factory=dict)
    warnings: list = dataclasses.field(default_factory=list)
    error: str = None


def _base_dir():
    """Directory the "next to the app" settings file lives in.

    Frozen: beside the exe at the ZIP root, so a user can edit the ini they can
    see. From source: the repository root (this file's parent's parent), not the
    package directory — an ini inside `whisper_paste/` would look like source.
    """
    if bundle.is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _user_dir():
    """Per-user settings directory, alongside where the log already goes."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "WhisperPaste")


def search_paths():
    """Candidate settings files, in precedence order (first existing wins)."""
    return [
        os.path.join(_base_dir(), CONFIG_FILENAME),
        os.path.join(_user_dir(), CONFIG_FILENAME),
    ]


def find_config_file(explicit_path=None):
    """Locate the settings file. Returns ``(path_or_None, error_or_None)``.

    An explicit ``--config`` short-circuits the search and, when it does not
    exist, is an error: the user named that file deliberately, so silently
    falling back to another one (or to defaults) would run with settings they
    believe are in force. A missing file from the search order is not an error —
    running with no settings file at all is the default experience.
    """
    if explicit_path:
        path = os.path.abspath(os.path.expandvars(os.path.expanduser(explicit_path)))
        if os.path.isfile(path):
            return path, None
        return None, f"Settings file not found: {path}\n\nUsing built-in defaults."

    for candidate in search_paths():
        if os.path.isfile(candidate):
            return candidate, None
    return None, None


def _read_text(path):
    """Read the file as text. Returns ``(text, warning_or_None)``.

    ``utf-8-sig`` because Notepad — still the likeliest editor for a file
    shipped next to an exe — writes a BOM, and a BOM left in place turns the
    first section header into garbage. A file saved in some other encoding is
    decoded with replacement rather than rejected: one stray accented character
    in a comment must not cost the user every setting in the file.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return (
            raw.decode("utf-8-sig", "replace"),
            "file is not valid UTF-8; undecodable characters were replaced. "
            "Save it as UTF-8.",
        )


def parse_file(path):
    """Parse one settings file into a `SettingsResult`. Never raises."""
    result = SettingsResult(path=path)

    try:
        text, note = _read_text(path)
    except OSError as exc:
        result.error = f"Could not read settings file {path}: {exc}\n\nUsing built-in defaults."
        return result
    if note:
        result.warnings.append(f"{path}: {note}")

    # interpolation=None: values legitimately contain '%' (log_dir =
    # %LOCALAPPDATA%\WhisperPaste\logs), which BasicInterpolation would reject.
    # strict=False: a duplicated key is a copy/paste slip, not a reason to throw
    # the whole file away — the last one wins, as in most ini readers.
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text, source=path)
    except (configparser.Error, ValueError) as exc:
        result.error = (
            f"Could not parse settings file {path}: {exc}\n\nUsing built-in defaults."
        )
        return result

    section = None
    for name in parser.sections():
        if name.strip().lower() == SECTION:
            section = name
        else:
            result.warnings.append(
                f"{path}: unknown section [{name}] ignored "
                f"(the only section read is [{SECTION}])."
            )
    if section is None:
        result.warnings.append(
            f"{path}: no [{SECTION}] section found — the file had no effect."
        )
        return result

    for key, raw_value in parser.items(section):
        # Hyphens accepted as well as underscores: `log-transcripts` mirrors the
        # CLI flag and is the likelier typo of the two.
        normalised = key.strip().lower().replace("-", "_")
        entry = _KEYS.get(normalised)
        if entry is None:
            result.warnings.append(
                f"{path}: unknown key '{key}' ignored "
                f"(valid keys: {', '.join(sorted(_KEYS))})."
            )
            continue
        attribute, convert = entry
        try:
            result.values[attribute] = convert(raw_value if raw_value is not None else "")
        except _Invalid as exc:
            result.warnings.append(
                f"{path}: invalid value for '{key}' ({raw_value!r}): {exc}. "
                "Using the default."
            )
    return result


def apply_settings(values):
    """Write parsed settings onto `config`.

    Only the attributes present in `values` are touched, which is what leaves
    room for CLI flags to be applied on top afterwards.
    """
    for attribute, value in values.items():
        setattr(config, attribute, value)


def load(explicit_path=None):
    """Find and parse the settings file. Never raises."""
    try:
        path, error = find_config_file(explicit_path)
        if error is not None:
            return SettingsResult(error=error)
        if path is None:
            return SettingsResult()
        return parse_file(path)
    except Exception as exc:  # pragma: no cover - last-resort safety net
        logger.exception("Failed to load settings file")
        return SettingsResult(
            error=f"Could not load the settings file: {exc}\n\nUsing built-in defaults."
        )


def load_and_apply(explicit_path=None):
    """Load the settings file and apply it to `config`. Never raises.

    Called before `_apply_args` so CLI flags win, and before logging is
    configured — hence the result object rather than log lines.
    """
    result = load(explicit_path)
    apply_settings(result.values)
    return result
