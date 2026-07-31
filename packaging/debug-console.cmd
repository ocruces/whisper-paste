@echo off
REM Run WhisperPaste with a visible console, to read errors.
REM
REM Double-click this, or run it from a terminal with the same flags you would
REM pass WhisperPaste.exe:
REM
REM     debug-console.cmd --model tiny --refine
REM
REM Why a separate WhisperPaste-debug.exe and not just this script wrapping
REM WhisperPaste.exe: the console/GUI subsystem is fixed at link time, and a
REM GUI-subsystem exe does not attach to the console that launched it. Measured,
REM not assumed - started from a real terminal, WhisperPaste.exe has
REM sys.stdout is None and GetConsoleWindow() == 0, so it can print nothing
REM anywhere and this .cmd would show an empty window. WhisperPaste-debug.exe is
REM the identical program linked as a console subsystem executable; both are
REM built from one PyInstaller Analysis and share the _internal folder.
REM
REM %~dp0 is this script's own directory (with a trailing backslash), so it
REM finds the exe wherever the ZIP was unpacked and whatever the current
REM working directory is - including the read-only "run from the Downloads
REM folder" case where the CWD is somewhere else entirely.

"%~dp0WhisperPaste-debug.exe" %*

REM Keep the window open. Without this, a startup crash paints its traceback and
REM the console closes with it, which is exactly the failure this file exists to
REM make readable. Also reports the exit code: 1 = already running, 2 = bad
REM command line or --gpu on a portable build.
echo.
echo WhisperPaste-debug.exe exited with code %ERRORLEVEL%.
pause
