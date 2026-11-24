# Ditto in Teams — Kawarimi no Jutsu 🥷💨🟣

Press `F9` during a meeting: a ninja smoke cloud covers your camera, and as it clears, a swaying Ditto appears in your place. Press `F9` again and you return.

You don't need a background photo (`fundo.jpg`). The scene is captured live at the moment of the trigger, so it works anywhere.

## 1. Setup (Once)

**Prerequisites:** Python 3.10+ and OBS Studio 28+ (already includes `obs-websocket` v5).

No need to install `ffmpeg` manually: the `imageio-ffmpeg` package already bundles a binary.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
