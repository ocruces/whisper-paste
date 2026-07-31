@echo off
REM Start WhisperPaste with a fixed dictation language (@@LANG@@).
REM
REM WhisperPaste.exe is normally started by double-clicking it, and a
REM double-click cannot pass command-line flags - so each language gets its own
REM little launcher next to the exe. Double-click this one to dictate in
REM @@LANG@@ instead of letting Whisper guess the language.
REM
REM GENERATED FILE - do not hand-edit this copy. scripts\build.ps1 expands
REM packaging\launcher-template.cmd once per language listed in its -Languages
REM parameter, so the next build overwrites whatever you change here. To ship a
REM different set of languages, build with e.g.:
REM
REM     scripts\build.ps1 -Languages en,es,fr,pt-br
REM
REM SWITCHING LANGUAGE: quit the running WhisperPaste first (right-click the
REM tray icon -> Quit). Only one copy may run at a time - it holds a named
REM Windows mutex - so launching a second one just shows an "already running"
REM message; it does not switch the language of the copy already running.
REM
REM Extra flags are passed straight through, so this still works:
REM
REM     WhisperPaste-@@LANG@@.cmd --refine
REM
REM and because argparse takes the last occurrence of a repeated option, you can
REM even override the language for one run:
REM
REM     WhisperPaste-@@LANG@@.cmd --lang fr
REM
REM Three details are load-bearing:
REM   start      - launches the exe and lets this console window close at once,
REM                instead of leaving a black box on screen for the whole
REM                session.
REM   ""         - the empty window title. Without it, start treats the first
REM                quoted argument (the exe path) as the title and then has no
REM                program left to run.
REM   %~dp0      - this script's own directory, with a trailing backslash, so it
REM                finds the exe wherever the ZIP was unpacked and whatever the
REM                current working directory is.

@start "" "%~dp0WhisperPaste.exe" --lang @@LANG@@ %*
