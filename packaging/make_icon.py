"""Regenerate packaging/whisper-paste.ico from the tray icon drawing.

Run this by hand after changing `create_icon_image`, from the dev venv:

    .venv\\Scripts\\python.exe packaging\\make_icon.py

The .ico is checked in as a binary rather than generated during the build, for
two reasons: PyInstaller needs a real file on disk at analysis time, and the
build must not depend on Pillow before the build venv exists.

The artwork is imported from `whisper_paste.app`, never reimplemented here —
the exe icon and the tray icon have to be the same drawing. `tests/
test_packaging.py` fails if the committed file and the code drift apart.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from whisper_paste.app import COLOR_IDLE, create_icon_image  # noqa: E402

DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper-paste.ico")

# Windows picks the closest frame for each context: 16px in the taskbar and
# title bar, 32px on the desktop, 256px in Explorer's extra-large view. Ship
# them all rather than let Windows downscale 256 to 16, which looks muddy.
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main():
    # The idle colour: the exe's identity should match the icon the user sees
    # in the tray when the app is sitting ready.
    image = create_icon_image(COLOR_IDLE, size=256)
    image.save(DEST, format="ICO", sizes=SIZES)
    print(f"Wrote {DEST} ({os.path.getsize(DEST):,} bytes, {len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
