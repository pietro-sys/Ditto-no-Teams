"""
ditto_kawarimi.py — "Kawarimi no Jutsu": aperte F9 e vire um Ditto no meio de uma nuvem de fumaça.

Não precisa de foto de fundo preparada. O cenário é capturado AO VIVO na hora do disparo
(screenshot da cena da webcam no OBS), congelado e usado como fundo do Ditto.

Modos (--modo):
  ao-vivo  (padrão) congela o cenário real no instante do F9 (ou o "cenário limpo" do F8)
  alpha    Ditto com canal alfa por cima da câmera ao vivo
  solido   Ditto sobre uma cor sólida (--bg-cor "#1e1e2e")

Teclas (globais, funcionam com o OBS/Teams em foco):
  F9   alterna Webcam_Normal <-> Modo_Ditto (com fumaça)
  F8   memoriza o "cenário limpo": saia do quadro, aperte F8 — o Ditto usa esse fundo sem você
  F10  troca o modo (ao-vivo -> alpha -> solido)
  Ctrl+C no terminal para sair

Exemplos:
  python ditto_kawarimi.py --setup           # cria cenas/fontes no OBS e já fica ouvindo o F9
  python ditto_kawarimi.py --senha MINHA_SENHA --vcam
  python ditto_kawarimi.py --preview         # testa sem OBS, numa janela com a webcam
  python ditto_kawarimi.py --preview --sem-camera --bg-cor "#1e1e2e"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path

import preparar_ditto as prep

PASTA = prep.PASTA
MODOS = ("ao-vivo", "alpha", "solido")
COR_PADRAO = "#1e1e2e"
FONTE_COR = "Ditto_Cor"
FONTE_FUNDO = "Ditto_Fundo"
FONTE_OVERLAY = "Ditto_Overlay"
FONTE_FUMACA = "Fumaca"


# --------------------------------------------------------------------------- #
# Assets (gerados automaticamente na primeira vez)
# --------------------------------------------------------------------------- #
def carregar_manifest(saida: Path, forcar: bool = False) -> dict:
    arq = saida / "manifest.json"
    m = json.loads(arq.read_text("utf-8")) if arq.exists() else None
    motivo = "forçado (--regenerar)" if forcar else None
    if m is None:
        motivo = "primeira execução"
    elif not motivo:
        origem = Path(m["ditto_origem"])
        fumaca_pasta = prep.achar_arquivo("fumaca", prep.EXT_FUMACA)
        if not Path(m["overlay_webm"]).exists() or not Path(m["fumaca_webm"]).exists():
            motivo = "arquivos gerados sumiram"
        elif not origem.exists() or origem.stat().st_mtime != m["ditto_mtime"]:
            motivo = "o Ditto mudou"
        elif m["fumaca_origem"] is None and fumaca_pasta is not None:
            motivo = f"novo {fumaca_pasta.name} encontrado"
    if motivo:
        print(f"Gerando assets do Ditto ({motivo})...")
        op = prep.Opcoes(**m["opcoes"]) if m else prep.Opcoes(saida=str(saida))
        op.saida = str(saida)
        if m and not Path(m["ditto_origem"]).exists():
            op.ditto = None  # procura de novo ditto.* na pasta
        if m and m["fumaca_origem"] is None:
            op.fumaca = None
        m = prep.gerar(op)
    return m


def cor_abgr(cor: tuple[int, int, int]) -> int:
    r, g, b = cor
    return 0xFF000000 | (b << 16) | (g << 8) | r


# --------------------------------------------------------------------------- #
# Atalhos globais (pynput)
# --------------------------------------------------------------------------- #
def iniciar_teclas(mapa: dict[str, callable]):
    """mapa: {'F9': funcao, ...}. Cada função roda numa thread própria."""
    try:
        from pynput import keyboard
    except ImportError:
        print("! pynput não instalado: atalhos globais desativados (pip install pynput)")
        return None

    def resolver(nome: str):
        n = nome.strip().lower()
        if hasattr(keyboard.Key, n):
            return getattr(keyboard.Key, n)
        if len(n) == 1:
            return keyboard.KeyCode.from_char(n)
        raise SystemExit(f"Tecla desconhecida: {nome!r} (use F1..F12, ou uma letra)")

    alvos = [(resolver(k), fn) for k, fn in mapa.items()]

    def ao_apertar(tecla):
        for alvo, fn in alvos:
            igual = tecla == alvo or (
                isinstance(tecla, keyboard.KeyCode) and isinstance(alvo, keyboard.KeyCode)
                and tecla.char is not None and alvo.char is not None
                and tecla.char.lower() == alvo.char
            )
            if igual:
                threading.Thread(target=fn, daemon=True).start()

    ouvinte = keyboard.Listener(on_press=ao_apertar)
    ouvinte.daemon = True
    ouvinte.start()
    return ouvinte


# --------------------------------------------------------------------------- #
# OBS (obs-websocket v5)
# --------------------------------------------------------------------------- #
class OBS:
    def __init__(self, a: argparse.Namespace, manifest: dict):
        try:
            import obsws_python as obsws
        except ImportError:
            raise SystemExit("Instale as dependências: pip install -r requirements.txt")
        logging.getLogger("obsws_python").setLevel(logging.CRITICAL)  # erros tratados aqui
        self.a, self.m = a, manifest
        self.saida = Path(a.saida).resolve()
        self.trava = threading.Lock()
        self.cenario_limpo: Path | None = None
        self.lado = "a"
        try:
            self.cl = obsws.ReqClient(host=a.host, port=a.porta, password=a.senha or "", timeout=5)
        except Exception as e:  # recusa de conexão, senha errada, timeout...
            raise SystemExit(
                f"Não consegui conectar ao OBS em {a.host}:{a.porta} ({type(e).__name__}: {e}).\n"
                "  -> Abra o OBS e ative Ferramentas > Configurações do Servidor WebSocket.\n"
                "  -> Confira porta e senha (--porta / --senha ou variável OBS_WS_SENHA)."
            )
        v = self.req("GetVersion")
        print(f"Conectado ao OBS {v['obsVersion']} (obs-websocket {v['obsWebSocketVersion']})")
        vs = self.req("GetVideoSettings")
        self.W, self.H = vs["baseWidth"], vs["baseHeight"]

    # -- helpers ----------------------------------------------------------- #
    def req(self, nome: str, **dados):
        return self.cl.send(nome, dados or None, raw=True) or {}

    def cenas(self) -> list[str]:
        return [s["sceneName"] for s in self.req("GetSceneList")["scenes"]]

    def inputs(self) -> dict[str, str]:
        return {i["inputName"]: i["inputKind"] for i in self.req("GetInputList")["inputs"]}

    def itens(self, cena: str) -> dict[str, int]:
        return {i["sourceName"]: i["sceneItemId"] for i in self.req("GetSceneItemList", sceneName=cena)["sceneItems"]}

    def cena_atual(self) -> str:
        return self.req("GetCurrentProgramScene")["currentProgramSceneName"]

    def garantir_item(self, cena: str, fonte: str) -> tuple[int, bool]:
        itens = self.itens(cena)
        if fonte in itens:
            return itens[fonte], False
        r = self.req("CreateSceneItem", sceneName=cena, sourceName=fonte, sceneItemEnabled=True)
        return r["sceneItemId"], True

    def garantir_input(self, cena: str, nome: str, tipo: str, config: dict) -> bool:
        ins = self.inputs()
        if nome in ins:
            if ins[nome] != tipo:
                print(f"  ! '{nome}' existe mas é do tipo {ins[nome]} (esperado {tipo}) — mantendo")
            else:
                self.req("SetInputSettings", inputName=nome, inputSettings=config, overlay=True)
            self.garantir_item(cena, nome)
            return False
        self.req("CreateInput", sceneName=cena, inputName=nome, inputKind=tipo,
                 inputSettings=config, sceneItemEnabled=True)
        print(f"  + fonte '{nome}' ({tipo}) em '{cena}'")
        return True

    def ajustar_tela(self, cena: str, fonte: str) -> None:
        item = self.itens(cena)[fonte]
        self.req("SetSceneItemTransform", sceneName=cena, sceneItemId=item, sceneItemTransform={
            "positionX": 0.0, "positionY": 0.0, "alignment": 5, "rotation": 0.0,
            "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
            "boundsWidth": float(self.W), "boundsHeight": float(self.H),
        })

    def copiar_transform(self, origem: str, destino: str, fonte: str) -> None:
        t = self.req("GetSceneItemTransform", sceneName=origem, sceneItemId=self.itens(origem)[fonte])
        t = {k: v for k, v in t["sceneItemTransform"].items()
             if k not in ("sourceWidth", "sourceHeight", "width", "height")}
        if t.get("boundsWidth", 0) < 1 or t.get("boundsHeight", 0) < 1:
            t.pop("boundsWidth", None)
            t.pop("boundsHeight", None)
        self.req("SetSceneItemTransform", sceneName=destino, sceneItemId=self.itens(destino)[fonte],
                 sceneItemTransform=t)

    def mostrar(self, cena: str, fonte: str, visivel: bool) -> None:
        itens = self.itens(cena)
        if fonte in itens:
            self.req("SetSceneItemEnabled", sceneName=cena, sceneItemId=itens[fonte], sceneItemEnabled=visivel)

    def screenshot(self, fonte: str, caminho: Path) -> Path:
        self.req("SaveSourceScreenshot", sourceName=fonte, imageFormat="png",
                 imageFilePath=str(caminho.resolve()), imageWidth=self.W, imageHeight=self.H)
        return caminho

    # -- setup ------------------------------------------------------------- #
    def setup(self) -> None:
        a, m = self.a, self.m
        print(f"Setup do OBS (canvas {self.W}x{self.H})")
        cenas = self.cenas()
        for c in (a.cena_normal, a.cena_ditto):
            if c not in cenas:
                self.req("CreateScene", sceneName=c)
                print(f"  + cena '{c}'")

        # Câmera
        if a.camera not in self.inputs():
            tipo = {"win32": "dshow_input", "darwin": "av_capture_input_v2"}.get(sys.platform, "v4l2_input")
            self.req("CreateInput", sceneName=a.cena_normal, inputName=a.camera, inputKind=tipo,
                     inputSettings={}, sceneItemEnabled=True)
            print(f"  + fonte '{a.camera}' ({tipo}) em '{a.cena_normal}'")
            self._escolher_primeira_camera(a.camera)
            self.ajustar_tela(a.cena_normal, a.camera)
        else:
            self.garantir_item(a.cena_normal, a.camera)
        _, novo = self.garantir_item(a.cena_ditto, a.camera)
        if novo:
            self.copiar_transform(a.cena_normal, a.cena_ditto, a.camera)

        # Fontes do Ditto
        cor = prep.parse_cor(a.bg_cor) or prep.parse_cor(COR_PADRAO)
        self.garantir_input(a.cena_ditto, FONTE_COR, "color_source_v3",
                            {"color": cor_abgr(cor), "width": self.W, "height": self.H})
        placeholder = self.saida / "fundo_a.png"
        if not placeholder.exists():
            from PIL import Image
            Image.new("RGB", (self.W, self.H), cor).save(placeholder)
        self.garantir_input(a.cena_ditto, FONTE_FUNDO, "image_source", {"file": str(placeholder)})
        overlay = m["overlay_mov"] if (a.usar_mov and m.get("overlay_mov")) else m["overlay_webm"]
        self.garantir_input(a.cena_ditto, FONTE_OVERLAY, "ffmpeg_source", {
            "is_local_file": True, "local_file": overlay, "looping": True,
            "restart_on_activate": True, "close_when_inactive": False,
        })
        # A mesma fumaça nas duas cenas: continua tocando durante a troca de cena
        self.garantir_input(a.cena_normal, FONTE_FUMACA, "ffmpeg_source", {
            "is_local_file": True, "local_file": m["fumaca_webm"], "looping": False,
            "restart_on_activate": False, "clear_on_media_end": True, "close_when_inactive": False,
        })
        self.garantir_item(a.cena_ditto, FONTE_FUMACA)
        self.req("TriggerMediaInputAction", inputName=FONTE_FUMACA,
                 mediaAction="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP")

        # Ordem (0 = embaixo) e enquadramento
        for i, nome in enumerate([FONTE_COR, a.camera, FONTE_FUNDO, FONTE_OVERLAY]):
            self.req("SetSceneItemIndex", sceneName=a.cena_ditto,
                     sceneItemId=self.itens(a.cena_ditto)[nome], sceneItemIndex=i)
        for cena in (a.cena_normal, a.cena_ditto):
            itens = self.itens(cena)
            self.req("SetSceneItemIndex", sceneName=cena, sceneItemId=itens[FONTE_FUMACA],
                     sceneItemIndex=len(itens) - 1)
            self.ajustar_tela(cena, FONTE_FUMACA)
        for f in (FONTE_FUNDO, FONTE_OVERLAY):
            self.ajustar_tela(a.cena_ditto, f)
        self.aplicar_modo()
        print("Setup concluído. Confira no OBS se a câmera certa aparece em "
              f"'{a.camera}' (clique duas vezes nela para escolher o dispositivo).")

    def _escolher_primeira_camera(self, nome: str) -> None:
        if sys.platform != "win32":
            return
        try:
            itens = self.req("GetInputPropertiesListPropertyItems", inputName=nome,
                             propertyName="video_device_id")["propertyItems"]
            disp = [i for i in itens if i.get("itemEnabled", True) and i.get("itemValue")]
            # evita escolher a própria câmera virtual do OBS
            disp.sort(key=lambda i: "obs" in i["itemName"].lower())
            if disp:
                self.req("SetInputSettings", inputName=nome,
                         inputSettings={"video_device_id": disp[0]["itemValue"]}, overlay=True)
                print(f"    câmera escolhida: {disp[0]['itemName']}")
        except Exception as e:
            print(f"    (não consegui escolher a câmera automaticamente: {e})")

    def verificar(self) -> bool:
        a = self.a
        faltando = [c for c in (a.cena_normal, a.cena_ditto) if c not in self.cenas()]
        ins = self.inputs()
        faltando += [f for f in (a.camera, FONTE_COR, FONTE_FUNDO, FONTE_OVERLAY, FONTE_FUMACA) if f not in ins]
        if faltando:
            print(f"! Faltam no OBS: {', '.join(faltando)}  ->  rode com --setup")
            return False
        print("OBS ok: cenas e fontes encontradas.")
        return True

    def camera_virtual(self) -> None:
        if self.req("GetVirtualCamStatus").get("outputActive"):
            print("Câmera virtual do OBS já está ligada.")
        else:
            self.req("StartVirtualCam")
            print("Câmera virtual do OBS ligada (no Teams escolha 'OBS Virtual Camera').")

    # -- efeito ------------------------------------------------------------ #
    def aplicar_modo(self) -> None:
        a = self.a
        self.mostrar(a.cena_ditto, FONTE_COR, a.modo == "solido")
        self.mostrar(a.cena_ditto, a.camera, a.modo == "alpha")
        self.mostrar(a.cena_ditto, FONTE_FUNDO, a.modo == "ao-vivo")

    def congelar_cenario(self) -> None:
        from PIL import Image, ImageFilter

        if self.cenario_limpo and self.cenario_limpo.exists():
            img, desfoque = Image.open(self.cenario_limpo), 0
        else:
            img = Image.open(self.screenshot(self.a.fonte_captura, self.saida / "_captura.png"))
            desfoque = self.a.desfoque
        img = img.convert("RGB")
        if desfoque > 0:
            img = img.filter(ImageFilter.GaussianBlur(desfoque))
        self.lado = "b" if self.lado == "a" else "a"  # nome alternado força o OBS a recarregar
        destino = self.saida / f"fundo_{self.lado}.png"
        img.save(destino)
        self.req("SetInputSettings", inputName=FONTE_FUNDO, inputSettings={"file": str(destino)}, overlay=True)

    def _poof(self, destino: str) -> None:
        if FONTE_FUMACA in self.inputs():
            self.req("TriggerMediaInputAction", inputName=FONTE_FUMACA,
                     mediaAction="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
            time.sleep(self.a.troca_em if self.a.troca_em is not None else self.m["troca_em"])
        self.req("SetCurrentProgramScene", sceneName=destino)

    def alternar(self) -> None:
        if not self.trava.acquire(blocking=False):
            return  # já está no meio de uma troca
        try:
            a = self.a
            if self.cena_atual() == a.cena_ditto:
                self._poof(a.cena_normal)
                print("  << de volta: " + a.cena_normal)
            else:
                if a.modo == "ao-vivo":
                    self.congelar_cenario()  # ANTES da fumaça, para o cenário sair limpo
                self.aplicar_modo()
                self._poof(a.cena_ditto)
                print(f"  >> Kawarimi! {a.cena_ditto} (modo {a.modo})")
        except Exception as e:
            print(f"  ! erro na troca: {e}")
        finally:
            self.trava.release()

    def memorizar_cenario(self) -> None:
        with self.trava:
            try:
                self.cenario_limpo = self.screenshot(self.a.fonte_captura, self.saida / "cenario_limpo.png")
                print("  [F8] cenário limpo memorizado — o Ditto vai usar este fundo.")
            except Exception as e:
                print(f"  ! não consegui capturar o cenário: {e}")

    def proximo_modo(self) -> None:
        self.a.modo = MODOS[(MODOS.index(self.a.modo) + 1) % len(MODOS)]
        try:
            if self.cena_atual() == self.a.cena_ditto:
                if self.a.modo == "ao-vivo":
                    self.congelar_cenario()
                self.aplicar_modo()
        except Exception as e:
            print(f"  ! {e}")
        print(f"  modo agora: {self.a.modo}")


# --------------------------------------------------------------------------- #
# Preview local (sem OBS): janela OpenCV com a webcam ou cor sólida
# --------------------------------------------------------------------------- #
def preview(a: argparse.Namespace, m: dict) -> None:
    import cv2
    import numpy as np

    cap = None
    if not a.sem_camera:
        api = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(a.camera_indice, api)
        ok, f = cap.read() if cap.isOpened() else (False, None)
        if not ok:
            print("! Webcam indisponível (o OBS ou o Teams podem estar usando). Usando cor sólida.")
            cap.release()
            cap = None
    if cap is not None:
        H, W = f.shape[:2]
    else:
        W, H = m["canvas"]
    op = prep.Opcoes(**m["opcoes"])
    ditto, fumaca, (px, py), *_ = prep.construir(op, W, H)
    troca = a.troca_em if a.troca_em is not None else fumaca.duracao * 0.38
    cor = np.array(prep.parse_cor(a.bg_cor) or prep.parse_cor(COR_PADRAO), np.float32) / 255
    solido = np.empty((H, W, 3), np.float32)
    solido[:] = cor

    pedidos: queue.Queue[str] = queue.Queue()
    iniciar_teclas({a.tecla: lambda: pedidos.put("alternar"),
                    a.tecla_cenario: lambda: pedidos.put("cenario"),
                    a.tecla_modo: lambda: pedidos.put("modo")})
    janela = "Ditto Kawarimi (preview)"
    print(f"Preview {W}x{H}: [{a.tecla}/espaço] alternar  [{a.tecla_cenario}/c] cenário limpo  "
          f"[{a.tecla_modo}/m] modo  [q/Esc] sair")
    eh_ditto, t0, destino, congelado, limpo, t_ditto = False, None, False, None, None, 0.0
    # F8/F9/F10 já chegam pelo atalho global (pynput); na janela também valem letras
    teclas_cv = {32: "alternar", ord("d"): "alternar", ord("c"): "cenario", ord("m"): "modo"}
    while True:
        if cap is not None:
            ok, f = cap.read()
            vivo = f[..., ::-1].astype(np.float32) / 255 if ok else solido.copy()
        else:
            vivo = solido.copy()
        agora = time.time()
        while not pedidos.empty():
            p = pedidos.get()
            if p == "alternar" and t0 is None:
                destino = not eh_ditto
                if destino:
                    if limpo is not None:
                        congelado = limpo.copy()
                    else:
                        congelado = cv2.GaussianBlur(vivo, (0, 0), a.desfoque) if a.desfoque > 0 else vivo.copy()
                t0 = agora
            elif p == "cenario":
                limpo = vivo.copy()
                print("  cenário limpo memorizado")
            elif p == "modo":
                a.modo = MODOS[(MODOS.index(a.modo) + 1) % len(MODOS)]
                print(f"  modo: {a.modo}")
        t = agora - t0 if t0 is not None else None
        if t is not None and t >= troca and eh_ditto != destino:
            eh_ditto, t_ditto = destino, agora
        if eh_ditto:
            base = {"ao-vivo": congelado, "alpha": vivo, "solido": solido}[a.modo].copy()
            td = agora - t_ditto
            prep.sobrepor(base, ditto.quadro(td, prep.escala_pop(td)), px, py)
        else:
            base = vivo
        if t is not None:
            prep.sobrepor(base, fumaca.quadro(t), 0, 0)
            if t >= fumaca.duracao:
                t0 = None
        cv2.imshow(janela, (np.clip(base, 0, 1)[..., ::-1] * 255).astype(np.uint8))
        k = cv2.waitKeyEx(1)
        if k in (ord("q"), 27) or cv2.getWindowProperty(janela, cv2.WND_PROP_VISIBLE) < 1:
            break
        if k in teclas_cv:
            pedidos.put(teclas_cv[k])
    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()


# --------------------------------------------------------------------------- #
def criar_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Kawarimi no Jutsu: alterna entre a webcam e o Ditto no OBS com um atalho.")
    g = ap.add_argument_group("OBS WebSocket (v5)")
    g.add_argument("--host", default=os.environ.get("OBS_WS_HOST", "localhost"))
    g.add_argument("--porta", "--port", type=int, default=int(os.environ.get("OBS_WS_PORTA", 4455)))
    g.add_argument("--senha", "--password", default=os.environ.get("OBS_WS_SENHA", ""),
                   help="senha do WebSocket (ou variável de ambiente OBS_WS_SENHA)")
    g.add_argument("--setup", action="store_true", help="cria/verifica cenas e fontes no OBS")
    g.add_argument("--so-setup", action="store_true", help="faz o setup e sai (não fica ouvindo o atalho)")
    g.add_argument("--vcam", action="store_true", help="liga a câmera virtual do OBS (para o Teams)")
    g.add_argument("--cena-normal", default="Webcam_Normal")
    g.add_argument("--cena-ditto", default="Modo_Ditto")
    g.add_argument("--camera", default="Camera", help="nome da fonte de webcam no OBS")
    g.add_argument("--fonte-captura", default=None,
                   help="fonte/cena usada para capturar o cenário (padrão: a cena normal)")
    g.add_argument("--usar-mov", action="store_true", help="usa ditto_overlay.mov em vez do .webm")

    e = ap.add_argument_group("Efeito")
    e.add_argument("--modo", choices=MODOS, default=None, help="ao-vivo (padrão), alpha ou solido")
    e.add_argument("--bg-cor", "--fundo-cor", dest="bg_cor", default=None,
                   help="cor do modo sólido (ex.: '#1e1e2e'); 'transparente' = modo alpha")
    e.add_argument("--desfoque", type=float, default=10,
                   help="desfoque do cenário congelado quando não há F8 (0 = nítido)")
    e.add_argument("--troca-em", type=float, default=None,
                   help="segundos após o início da fumaça para trocar a cena (padrão: pico da fumaça)")
    e.add_argument("--tecla", default="F9", help="atalho para alternar (padrão F9)")
    e.add_argument("--tecla-cenario", default="F8", help="atalho para memorizar o cenário limpo")
    e.add_argument("--tecla-modo", default="F10", help="atalho para trocar o modo")
    e.add_argument("--saida", default=str(PASTA / "saida"), help="pasta dos assets gerados")
    e.add_argument("--regenerar", action="store_true", help="refaz os assets com preparar_ditto.py")

    p = ap.add_argument_group("Preview sem OBS")
    p.add_argument("--preview", action="store_true", help="testa o efeito numa janela, sem OBS")
    p.add_argument("--sem-camera", action="store_true", help="preview sem webcam (fundo sólido)")
    p.add_argument("--camera-indice", type=int, default=0)
    return ap


def main(argv: list[str] | None = None) -> None:
    a = criar_parser().parse_args(argv)
    if a.modo is None:
        if a.bg_cor and prep.parse_cor(a.bg_cor) is None:
            a.modo = "alpha"
        elif a.bg_cor:
            a.modo = "solido"
        else:
            a.modo = "ao-vivo"
    a.fonte_captura = a.fonte_captura or a.cena_normal
    m = carregar_manifest(Path(a.saida), a.regenerar)

    if a.preview or a.sem_camera:
        preview(a, m)
        return

    obs = OBS(a, m)
    if a.setup or a.so_setup:
        obs.setup()
    else:
        obs.verificar()
    if a.vcam:
        obs.camera_virtual()
    if a.so_setup:
        return

    ouvinte = iniciar_teclas({a.tecla: obs.alternar, a.tecla_cenario: obs.memorizar_cenario,
                              a.tecla_modo: obs.proximo_modo})
    print(f"\nPronto! [{a.tecla}] Kawarimi  [{a.tecla_cenario}] memorizar cenário limpo  "
          f"[{a.tecla_modo}] trocar modo (atual: {a.modo})  [Ctrl+C] sair")
    if a.modo == "ao-vivo":
        print("Dica: saia do quadro e aperte F8 para o Ditto aparecer no cenário sem você atrás.")
    try:
        while ouvinte is None or ouvinte.is_alive():
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\nAté a próxima, ninja.")


if __name__ == "__main__":
    main(sys.argv[1:])
