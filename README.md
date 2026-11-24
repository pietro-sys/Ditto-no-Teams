Ditto on Teams — Kawarimi no Jutsu 🥷💨🟣
Press F9 during a meeting: a cloud of ninja smoke covers your camera and, when it clears, a wobbling Ditto is sitting where you were. Press F9 again to come back.

You don't need a background photo (fundo.jpg). The background is captured live, at the moment you trigger the effect, so it works anywhere.

1. Installation (one time)
Requirements: Python 3.10+ and OBS Studio 28+ (which ships with obs-websocket v5). You don't need to install ffmpeg: the imageio-ffmpeg package bundles one.

py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
2. Put the files in the folder
File	Required?	Notes
ditto.png / ditto.gif / ditto.webp	yes	It can have a white or flat background: it's cut out automatically. Animated GIFs keep their animation.
fumaca.mp4 / fumaca.gif / fumaca.webm	no	Smoke on a black or green background is keyed out automatically. Without this file, smoke is generated for you.
On the first run, the assets are generated automatically in saida/. To generate or tweak them by hand:

python preparar_ditto.py
This produces:

ditto_overlay.webm: Ditto with an alpha channel, breathing and swaying in a loop (mode a). Use --mov to also get a ProRes 4444 version.
fumaca_alpha.webm: the smoke with alpha.
ditto_solido.mp4: only with --bg-cor "#1e1e2e". A quick test over a solid colour (mode c).
ditto_preview.png: a single frame to check the cutout.
Useful tweaks: --escala 0.7 (bigger Ditto), --pos-x 0.4, --respiracao 0.06, --balanco 3, --resolucao 1920x1080, --remover-fundo nao, --tolerancia 25.

3. Test without OBS (optional)
python ditto_kawarimi.py --preview
Opens a window with your webcam. Space/F9 toggles, C/F8 saves the clean background, M/F10 changes the mode and Q quits. Without a camera: python ditto_kawarimi.py --preview --sem-camera --bg-cor "#1e1e2e".

4. OBS
Open OBS and go to Tools → WebSocket Server Settings. Check Enable WebSocket server (port 4455) and copy the password.
Run the setup. It creates any missing scenes and sources, then keeps listening for F9:
python ditto_kawarimi.py --setup --senha YOUR_PASSWORD --vcam
The setup creates or checks:

Scene	Sources (bottom to top)
Webcam_Normal	Camera (your webcam), Fumaca
Modo_Ditto	Ditto_Cor, Camera, Ditto_Fundo (frozen background), Ditto_Overlay (Ditto with alpha), Fumaca
If the wrong camera shows up, double-click Camera in OBS and pick the right device. From then on, just run python ditto_kawarimi.py --senha YOUR_PASSWORD (or set $env:OBS_WS_SENHA). For another host or port: --host 192.168.0.10 --porta 4455.

The three modes (--modo, or F10 to cycle)
Mode	What shows behind Ditto	When to use it
ao-vivo (default)	the real background, frozen at the moment you press F9	anywhere, with no preparation
alpha	the live camera (Ditto overlaid with alpha)	when you want to stay visible
solido	a solid colour (--bg-cor "#1e1e2e")	for quick tests or with the camera off
The F8 trick (clean background): in ao-vivo mode, the frozen image still has you in it (that's why it's slightly blurred; see --desfoque). To make Ditto truly take your place, step out of frame for a second and press F8. From then on, F9 uses that background without you in it. It lasts until you close the script. Press F8 again if you move somewhere else.

5. Teams
In OBS, click Start Virtual Camera (or use --vcam).
In Teams, go to Settings → Devices → Camera and choose OBS Virtual Camera.
Keep ditto_kawarimi.py running in the terminal. F9 works even while Teams has focus.
Teams only mirrors your own preview. Other participants see the image the right way round.

Keys
Key	Action
F9	Kawarimi: toggles Webcam_Normal ↔ Modo_Ditto with smoke
F8	saves the clean background (step out of frame first)
F10	changes the mode: ao-vivo → alpha → solido
Ctrl+C	stops the script
You can rebind them with --tecla F7, --tecla-cenario F6 and --tecla-modo F11.

Troubleshooting
"Não consegui conectar ao OBS" (couldn't connect to OBS): OBS must be open with the WebSocket server enabled. Check the port and password.
The smoke doesn't show up: run --setup again. The Fumaca source must be at the top of both scenes.
White halo around Ditto: python preparar_ditto.py --tolerancia 60. If the image already has transparency: --remover-fundo nao.
The swap happens before or after the smoke peaks: adjust it with --troca-em 0.6 (in seconds).
Webcam busy in --preview: OBS or Teams is using the camera. Close them or use --sem-camera.
