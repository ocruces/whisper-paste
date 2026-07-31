"""PyInstaller entry point for the portable build.

Deliberately not one of the two obvious candidates:

  - not ``whisper_paste/__main__.py``: it calls ``main()`` at module scope with
    no ``if __name__`` guard, so PyInstaller's bootstrap would start the tray
    while the module was still being set up;
  - not ``whisper_paste/app.py`` directly: PyInstaller renames the entry script
    to ``__main__``, so the package would later import a *second* copy of the
    module under its real name — two sets of the module globals that app.py's
    state machine depends on being singular.

A separate one-line launcher outside the package avoids both.
"""

from whisper_paste.app import main

if __name__ == "__main__":
    main()
