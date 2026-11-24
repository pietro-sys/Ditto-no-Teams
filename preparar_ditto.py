"""
preparar_ditto.py — gera os assets do "Kawarimi no Jutsu" virando Ditto,
SEM precisar de uma foto de fundo (fundo.jpg) preparada antes.


Entrada: só o Ditto (ditto.png / ditto.gif / ditto.webp) e, opcionalmente,
um vídeo de fumaça (fumaca.mp4 / fumaca.gif / fumaca.webm).

Saídas (pasta ./saida):
  a) ditto_overlay.webm   Ditto animado (respiração + balanço) com canal alfa
     ditto_overlay.mov    (opcional, --mov) mesmo conteúdo em ProRes 4444
     ditto_preview.png    um quadro com alfa, para conferir o recorte
  b) fumaca_alpha.webm    fumaça com alfa (gerada proceduralmente ou a partir
                          do seu fumaca.*, com chroma/luma key automático)
     -> o cenário "ao vivo" é capturado na hora do disparo pelo ditto_kawarimi.py
  c) ditto_solido.mp4     (opcional, --bg-cor #1e1e2e) teste rápido sem câmera:
                          fumaça + Ditto sobre cor sólida
  manifest.json           parâmetros usados (lido pelo ditto_kawarimi.py)

Uso rápido:
  python preparar_ditto.py
  python preparar_ditto.py --bg-cor "#1e1e2e" --mov
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageSequence

PASTA = Path(__file__).resolve().parent
EXT_DITTO = (".png", ".gif", ".webp", ".apng")
EXT_FUMACA = (".webm", ".mp4", ".mov", ".gif", ".mkv", ".avi")


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def achar_arquivo(nome_base: str, extensoes: tuple[str, ...]) -> Path | None:
    for ext in extensoes:
        p = PASTA / f"{nome_base}{ext}"
        if p.exists():
            return p
    return None


def ffmpeg_exe() -> str | None:
    """ffmpeg embutido (imageio-ffmpeg, tem VP9 com alfa e ProRes) ou o do sistema."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def parse_cor(texto: str | None) -> tuple[int, int, int] | None:
    """'#1e1e2e' / '1e1e2e' / '30,30,46' -> (r, g, b). 'transparente'/None -> None."""
    if not texto or texto.lower() in ("transparente", "transparent", "none", "alpha"):
        return None
    t = texto.strip().lstrip("#")
    if "," in t:
        r, g, b = (int(v) for v in t.split(","))
        return r, g, b
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6:
        raise ValueError(f"Cor inválida: {texto!r} (use #RRGGBB)")
    return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)


def suave(a: float, b: float, x):
    """smoothstep"""
    t = np.clip((x - a) / max(b - a, 1e-6), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def escala_pop(td: float) -> float:
    """'Pop' de entrada do Ditto: cresce de 60% a ~106% e assenta em 100% em ~0,5 s."""
    if td >= 0.5:
        return 1.0
    return 0.6 + 0.4 * float(suave(0, 0.3, td)) + 0.06 * math.sin(td * 2 * math.pi)


def premultiplicar(rgba_u8: np.ndarray) -> np.ndarray:
    f = rgba_u8.astype(np.float32) / 255.0
    f[..., :3] *= f[..., 3:4]
    return f


def para_rgba_u8(prem: np.ndarray) -> np.ndarray:
    """premultiplicado float 0..1 -> RGBA uint8 com alfa 'reto' (o que o ffmpeg espera)."""
    a = prem[..., 3:4]
    rgb = np.where(a > 1e-4, prem[..., :3] / np.maximum(a, 1e-4), 0.0)
    out = np.concatenate([rgb, a], axis=2)
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def sobrepor(base: np.ndarray, prem: np.ndarray, x: int, y: int) -> None:
    """Compõe `prem` (RGBA premultiplicado) sobre `base` (float 0..1, 3 ou 4 canais) in-place."""
    H, W = base.shape[:2]
    h, w = prem.shape[:2]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, W), min(y + h, H)
    if x0 >= x1 or y0 >= y1:
        return
    src = prem[y0 - y : y1 - y, x0 - x : x1 - x]
    dst = base[y0:y1, x0:x1]
    inv = 1.0 - src[..., 3:4]
    if base.shape[2] == 4:
        dst[:] = src + dst * inv
    else:
        dst[:] = src[..., :3] + dst * inv


# --------------------------------------------------------------------------- #
# Ditto: carregar, recortar fundo, animar
# --------------------------------------------------------------------------- #
def remover_fundo(rgba: np.ndarray, tolerancia: int = 40) -> np.ndarray:
    """Remove o fundo liso (ex.: branco do GIF) por 'flood fill' a partir das bordas.
    Como o Ditto tem contorno escuro, o preenchimento para nele."""
    rgb = rgba[..., :3].astype(np.int16)
    borda = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    cor_fundo = np.median(borda, axis=0)
    parecido = (np.abs(rgb - cor_fundo).max(axis=2) <= tolerancia).astype(np.uint8)
    _, rotulos = cv2.connectedComponents(parecido, connectivity=4)
    da_borda = np.unique(np.concatenate([rotulos[0], rotulos[-1], rotulos[:, 0], rotulos[:, -1]]))
    da_borda = da_borda[da_borda != 0]  # 0 = pixels "diferentes" do fundo
    fundo = np.isin(rotulos, da_borda)
    alfa = np.where(fundo, 0, 255).astype(np.uint8)
    alfa = cv2.erode(alfa, np.ones((2, 2), np.uint8))  # tira o halo claro
    alfa = cv2.GaussianBlur(alfa, (3, 3), 0)  # borda suave
    out = rgba.copy()
    out[..., 3] = np.minimum(rgba[..., 3], alfa)
    return out


def carregar_ditto(caminho: Path, remover: str = "auto", tolerancia: int = 40):
    """Retorna (quadros RGBA uint8 recortados, duração de cada quadro em s)."""
    im = Image.open(caminho)
    quadros, duracoes = [], []
    for q in ImageSequence.Iterator(im):
        quadros.append(np.array(q.convert("RGBA")))
        d = q.info.get("duration", im.info.get("duration", 100)) or 100
        duracoes.append(max(d, 20) / 1000.0)

    opaco = all(q[..., 3].min() == 255 for q in quadros)
    if remover == "sim" or (remover == "auto" and opaco):
        quadros = [remover_fundo(q, tolerancia) for q in quadros]

    # recorta todos os quadros pela mesma caixa (união dos alfas)
    uniao = np.zeros(quadros[0].shape[:2], bool)
    for q in quadros:
        uniao |= q[..., 3] > 8
    ys, xs = np.where(uniao)
    if len(xs) == 0:
        raise SystemExit("O Ditto ficou 100% transparente — tente --remover-fundo nao ou --tolerancia menor.")
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    quadros = [q[y0:y1, x0:x1] for q in quadros]
    return quadros, duracoes


class DittoAnimado:
    """Ditto com 'respiração' (estica/achata) + balanço suave, ancorado na base."""

    def __init__(self, quadros, duracoes, altura_px: int, respiracao=0.04, balanco=2.0, periodo=2.0):
        h0, w0 = quadros[0].shape[:2]
        k = altura_px / h0
        tam = (max(1, round(w0 * k)), max(1, round(h0 * k)))
        interp = cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC
        self.quadros = [cv2.resize(premultiplicar(q), tam, interpolation=interp) for q in quadros]
        self.duracoes = duracoes
        self.ciclo_gif = float(sum(duracoes))
        self.resp, self.balanco, self.periodo = respiracao, balanco, periodo
        self.w, self.h = tam
        # caixa fixa com folga para esticar/girar
        folga = 1 + respiracao * 2 + math.sin(math.radians(abs(balanco))) + 0.1
        self.caixa_w = int(self.w * folga) + 4
        self.caixa_h = int(self.h * (1 + respiracao * 2 + 0.06)) + 4

    def duracao_loop(self) -> float:
        """Duração que fecha o loop do balanço (2 períodos) e, se possível, do GIF."""
        base = self.periodo * 2
        if len(self.quadros) > 1 and self.ciclo_gif > 0:
            n = max(1, round(base / self.ciclo_gif))
            return n * self.ciclo_gif
        return base

    def _indice(self, t: float) -> int:
        if len(self.quadros) == 1:
            return 0
        t = t % self.ciclo_gif
        acc = 0.0
        for i, d in enumerate(self.duracoes):
            acc += d
            if t < acc:
                return i
        return len(self.quadros) - 1

    def quadro(self, t: float, escala_extra: float = 1.0, periodo: float | None = None) -> np.ndarray:
        """RGBA premultiplicado (caixa_h x caixa_w), Ditto com a base centrada no rodapé da caixa."""
        p = periodo or self.periodo
        img = self.quadros[self._indice(t)]
        s = math.sin(2 * math.pi * t / p)
        sy = (1 + self.resp * s) * escala_extra
        sx = (1 - self.resp * 0.6 * s) * escala_extra
        ang = math.radians(self.balanco * math.sin(2 * math.pi * t / (2 * p)))
        bob = -0.012 * self.h * s
        cos, sin = math.cos(ang), math.sin(ang)
        # destino = T(ancora_dst) * R * S * T(-ancora_src)
        ax, ay = self.w / 2, self.h
        dx, dy = self.caixa_w / 2, self.caixa_h - 2 + bob
        M = np.array(
            [
                [cos * sx, -sin * sy, 0.0],
                [sin * sx, cos * sy, 0.0],
            ],
            np.float32,
        )
        M[:, 2] = np.array([dx, dy]) - M[:, :2] @ np.array([ax, ay])
        return cv2.warpAffine(
            img, M, (self.caixa_w, self.caixa_h), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
        )

    def posicao(self, W: int, H: int, pos_x: float, base_y: float) -> tuple[int, int]:
        """Canto superior esquerdo da caixa no quadro W x H."""
        return int(W * pos_x - self.caixa_w / 2), int(H * base_y - self.caixa_h)


# --------------------------------------------------------------------------- #
# Fumaça
# --------------------------------------------------------------------------- #
class FumacaProcedural:
    """'Puf' de fumaça ninja gerado por partículas (não precisa de arquivo)."""

    def __init__(self, W: int, H: int, duracao=1.4, semente=7, centro=(0.5, 0.56)):
        self.W, self.H, self.duracao = W, H, duracao
        self.lw = 320
        self.lh = max(2, round(320 * H / W))
        rng = np.random.default_rng(semente)
        n = 56
        lw, lh = self.lw, self.lh
        self.cx, self.cy = centro[0] * lw, centro[1] * lh
        self.ang = rng.uniform(0, 2 * np.pi, n)
        self.dist = np.sqrt(rng.uniform(0.0, 1.0, n)) * 0.40 * lw
        self.r0 = rng.uniform(0.04, 0.07, n) * lw
        self.r1 = self.r0 + rng.uniform(0.07, 0.13, n) * lw
        self.atraso = rng.uniform(0, 0.10, n) * duracao
        self.sobe = rng.uniform(0.02, 0.10, n) * lh
        self.peso = rng.uniform(0.7, 1.3, n)
        ruido = rng.random((lh * 2, lw * 2)).astype(np.float32)
        ruido = cv2.GaussianBlur(ruido, (0, 0), 6)
        self.ruido = (ruido - ruido.min()) / (np.ptp(ruido) + 1e-6)
        self.yy, self.xx = np.mgrid[0:lh, 0:lw].astype(np.float32)

    def quadro(self, t: float) -> np.ndarray:
        """RGBA premultiplicado float32 W x H."""
        d = self.duracao
        p = np.clip((t - self.atraso) / (0.55 * d), 0, 1)
        e = 1 - (1 - p) ** 3
        cx = self.cx + np.cos(self.ang) * self.dist * e
        cy = self.cy + np.sin(self.ang) * self.dist * 0.7 * e - self.sobe * (t / d)
        r = self.r0 + (self.r1 - self.r0) * e
        dx = self.xx[None] - cx[:, None, None]
        dy = self.yy[None] - cy[:, None, None]
        dens = (self.peso[:, None, None] * np.exp(-(dx * dx + dy * dy) / (r * r)[:, None, None])).sum(0)
        env = float(suave(0, 0.26 * d, t) * (1 - suave(0.48 * d, d, t)))
        ox = int(t / d * self.lw * 0.5) % self.lw
        n = self.ruido[: self.lh, ox : ox + self.lw]
        # ruído "come" as bordas -> contorno de nuvem em vez de elipse lisa
        alfa = (1 - np.exp(-dens * (1.6 + 2.4 * n))) * env
        alfa = np.clip((alfa - 0.08) / 0.92, 0, 1)
        sombra = np.clip(dens / (dens.max() + 1e-6), 0, 1)
        tom = 0.70 + 0.26 * n + 0.06 * (1 - self.yy / self.lh) - 0.06 * sombra
        rgb = np.stack([tom * 0.96, tom * 0.96, tom * 0.99], axis=2)
        prem = np.concatenate([rgb * alfa[..., None], alfa[..., None]], axis=2).astype(np.float32)
        return cv2.resize(prem, (self.W, self.H), interpolation=cv2.INTER_CUBIC).clip(0, 1)


def _chave_automatica(rgb: np.ndarray) -> str:
    borda = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]).astype(np.int16)
    r, g, b = np.median(borda, axis=0)
    if max(r, g, b) < 60:
        return "preto"
    if g > r + 40 and g > b + 40:
        return "verde"
    return "nenhuma"


def chavear(rgba_u8: np.ndarray, modo: str) -> np.ndarray:
    """Converte um quadro de fumaça em RGBA premultiplicado float conforme a chave."""
    f = rgba_u8.astype(np.float32) / 255.0
    rgb, a = f[..., :3], f[..., 3:4]
    if modo == "preto":  # fumaça clara sobre preto: brilho vira alfa
        lum = rgb.max(axis=2, keepdims=True)
        a = a * np.clip((lum - 0.04) / 0.75, 0, 1)
        cor = rgb / np.maximum(lum, 1e-3)  # normaliza a cor (vira fumaça clara)
        cor = 0.75 + 0.25 * cor
        return np.concatenate([cor * a, a], axis=2)
    if modo == "verde":
        r, g, b = rgb[..., 0:1], rgb[..., 1:2], rgb[..., 2:3]
        chave = g - np.maximum(r, b)
        a = a * (1 - np.clip((chave - 0.08) / 0.25, 0, 1))
        g2 = np.minimum(g, np.maximum(r, b))  # tira o vazamento de verde
        cor = np.concatenate([r, g2, b], axis=2)
        return np.concatenate([cor * a, a], axis=2)
    return np.concatenate([rgb * a, a], axis=2)


class FumacaArquivo:
    """Fumaça a partir de fumaca.mp4/.gif/.webm (fundo preto/verde é removido automaticamente)."""

    def __init__(self, caminho: Path, W: int, H: int, chave="auto"):
        self.W, self.H = W, H
        quadros, fps = self._ler(caminho)
        if not quadros:
            raise SystemExit(f"Não consegui ler quadros de {caminho}")
        tem_alfa = any(q[..., 3].min() < 250 for q in quadros[:: max(1, len(quadros) // 8)])
        if chave == "auto":
            chave = "nenhuma" if tem_alfa else _chave_automatica(quadros[len(quadros) // 2][..., :3])
        self.chave = chave
        self.fps = fps
        self.duracao = len(quadros) / fps
        # guarda em resolução reduzida para economizar memória; amplia na hora
        self.lw = min(640, W)
        self.lh = max(2, round(self.lw * H / W))
        self.quadros = [self._cobrir(chavear(q, chave), self.lw, self.lh) for q in quadros]

    @staticmethod
    def _ler(caminho: Path):
        if caminho.suffix.lower() == ".gif":
            im = Image.open(caminho)
            qs = [np.array(q.convert("RGBA")) for q in ImageSequence.Iterator(im)]
            dur = (im.info.get("duration", 40) or 40) / 1000
            return qs, 1 / dur
        cap = cv2.VideoCapture(str(caminho))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        qs = []
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA)
            qs.append(rgba)
        cap.release()
        return qs, fps

    @staticmethod
    def _cobrir(img: np.ndarray, W: int, H: int) -> np.ndarray:
        h, w = img.shape[:2]
        k = max(W / w, H / h)
        img = cv2.resize(img, (max(W, round(w * k)), max(H, round(h * k))), interpolation=cv2.INTER_AREA)
        y = (img.shape[0] - H) // 2
        x = (img.shape[1] - W) // 2
        return img[y : y + H, x : x + W].astype(np.float32)

    def quadro(self, t: float) -> np.ndarray:
        i = int(t * self.fps)
        if i < 0 or i >= len(self.quadros):
            return np.zeros((self.H, self.W, 4), np.float32)
        return cv2.resize(self.quadros[i], (self.W, self.H), interpolation=cv2.INTER_LINEAR).clip(0, 1)


# --------------------------------------------------------------------------- #
# Opções / construção / gravação
# --------------------------------------------------------------------------- #
@dataclass
class Opcoes:
    ditto: str | None = None
    fumaca: str | None = None
    saida: str = str(PASTA / "saida")
    largura: int = 1280
    altura: int = 720
    fps: int = 30
    escala: float = 0.62  # altura do Ditto em relação ao quadro
    pos_x: float = 0.5
    base_y: float = 1.0  # onde fica a "base" do Ditto (1.0 = rodapé)
    respiracao: float = 0.04
    balanco: float = 2.0
    periodo: float = 2.0
    remover_fundo: str = "auto"
    tolerancia: int = 40
    fumaca_chave: str = "auto"
    fumaca_duracao: float = 1.4
    bg_cor: str | None = None
    mov: bool = False
    png_seq: bool = False
    extras: dict = field(default_factory=dict)


def construir(op: Opcoes, W: int | None = None, H: int | None = None):
    """Monta (ditto_animado, fumaca, posicao_ditto) para um quadro W x H."""
    W = W or op.largura
    H = H or op.altura
    caminho_ditto = Path(op.ditto) if op.ditto else achar_arquivo("ditto", EXT_DITTO)
    if not caminho_ditto or not caminho_ditto.exists():
        raise SystemExit("Coloque um ditto.png (ou .gif/.webp) na pasta do projeto ou use --ditto CAMINHO.")
    quadros, dur = carregar_ditto(caminho_ditto, op.remover_fundo, op.tolerancia)
    ditto = DittoAnimado(quadros, dur, int(H * op.escala), op.respiracao, op.balanco, op.periodo)

    caminho_fumaca = Path(op.fumaca) if op.fumaca else achar_arquivo("fumaca", EXT_FUMACA)
    if caminho_fumaca and caminho_fumaca.exists():
        fumaca = FumacaArquivo(caminho_fumaca, W, H, op.fumaca_chave)
    else:
        fumaca = FumacaProcedural(W, H, op.fumaca_duracao)
    return ditto, fumaca, ditto.posicao(W, H, op.pos_x, op.base_y), caminho_ditto, caminho_fumaca


PERFIS = {
    "webm_alfa": ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "28",
                  "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-auto-alt-ref", "0"],
    "mov_alfa": ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                 "-vendor", "apl0", "-qscale:v", "11"],
    "mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium",
            "-movflags", "+faststart"],
}


class Gravador:
    """Envia quadros RGBA uint8 para o ffmpeg por pipe (ou salva PNGs se não houver ffmpeg)."""

    def __init__(self, caminho: Path, W: int, H: int, fps: int, perfil: str):
        self.caminho, self.i = caminho, 0
        exe = ffmpeg_exe()
        self.pasta_png = None
        if exe is None:
            self.pasta_png = caminho.with_suffix("")
            self.pasta_png.mkdir(parents=True, exist_ok=True)
            print(f"  ! ffmpeg não encontrado: salvando sequência PNG em {self.pasta_png}")
            self.p = None
            return
        cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
               "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", *PERFIS[perfil], str(caminho)]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def escrever(self, rgba_u8: np.ndarray) -> None:
        if self.p is None:
            Image.fromarray(rgba_u8, "RGBA").save(self.pasta_png / f"{self.i:05d}.png")
        else:
            self.p.stdin.write(np.ascontiguousarray(rgba_u8).tobytes())
        self.i += 1

    def fechar(self) -> None:
        if self.p is not None:
            self.p.stdin.close()
            if self.p.wait() != 0:
                raise SystemExit(f"ffmpeg falhou ao gerar {self.caminho}")


def gerar(op: Opcoes) -> dict:
    W, H, fps = op.largura, op.altura, op.fps
    saida = Path(op.saida)
    saida.mkdir(parents=True, exist_ok=True)
    ditto, fumaca, (px, py), cam_ditto, cam_fumaca = construir(op)
    print(f"Ditto:   {cam_ditto.name} ({len(ditto.quadros)} quadro(s))")
    print(f"Fumaça:  {cam_fumaca.name + ' (chave: ' + fumaca.chave + ')' if cam_fumaca else 'procedural (gerada)'}")
    print(f"Quadro:  {W}x{H} @ {fps} fps  ->  {saida}")

    # a) Ditto com alfa (loop contínuo)
    dur_loop = ditto.duracao_loop()
    periodo = dur_loop / 2
    n = max(1, round(dur_loop * fps))
    perfis = [("ditto_overlay.webm", "webm_alfa")] + ([("ditto_overlay.mov", "mov_alfa")] if op.mov else [])
    grav = [Gravador(saida / nome, W, H, fps, perfil) for nome, perfil in perfis]
    t0 = time.time()
    for i in range(n):
        tela = np.zeros((H, W, 4), np.float32)
        sobrepor(tela, ditto.quadro(i / fps, periodo=periodo), px, py)
        u8 = para_rgba_u8(tela)
        for g in grav:
            g.escrever(u8)
        if i == 0:
            Image.fromarray(u8, "RGBA").save(saida / "ditto_preview.png")
    for g in grav:
        g.fechar()
    print(f"  ok  {', '.join(n for n, _ in perfis)}  ({dur_loop:.2f}s em loop, {time.time() - t0:.1f}s)")

    # b) Fumaça com alfa
    n_f = max(1, round(fumaca.duracao * fps))
    g = Gravador(saida / "fumaca_alpha.webm", W, H, fps, "webm_alfa")
    for i in range(n_f):
        g.escrever(para_rgba_u8(fumaca.quadro(i / fps)))
    g.fechar()
    troca_em = round(fumaca.duracao * 0.38, 3)
    print(f"  ok  fumaca_alpha.webm  ({fumaca.duracao:.2f}s, troca em {troca_em:.2f}s)")

    # c) Teste em cor sólida
    cor = parse_cor(op.bg_cor)
    if cor is not None:
        fundo = np.array(cor, np.float32) / 255.0
        g = Gravador(saida / "ditto_solido.mp4", W, H, fps, "mp4")
        total = fumaca.duracao + dur_loop * 2
        for i in range(round(total * fps)):
            t = i / fps
            tela = np.empty((H, W, 4), np.float32)
            tela[..., :3] = fundo
            tela[..., 3] = 1
            if t >= troca_em:
                td = t - troca_em
                sobrepor(tela, ditto.quadro(td, escala_pop(td), periodo), px, py)
            sobrepor(tela, fumaca.quadro(t), 0, 0)
            g.escrever(para_rgba_u8(tela))
        g.fechar()
        print(f"  ok  ditto_solido.mp4  (fundo {op.bg_cor})")

    manifest = {
        "canvas": [W, H],
        "fps": fps,
        "overlay_webm": str((saida / "ditto_overlay.webm").resolve()),
        "overlay_mov": str((saida / "ditto_overlay.mov").resolve()) if op.mov else None,
        "fumaca_webm": str((saida / "fumaca_alpha.webm").resolve()),
        "fumaca_duracao": round(fumaca.duracao, 3),
        "troca_em": troca_em,
        "ditto_origem": str(cam_ditto.resolve()),
        "ditto_mtime": cam_ditto.stat().st_mtime,
        "fumaca_origem": str(cam_fumaca.resolve()) if cam_fumaca else None,
        "opcoes": asdict(op),
    }
    (saida / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ok  manifest.json")
    return manifest


def criar_parser() -> argparse.ArgumentParser:
    d = Opcoes()
    ap = argparse.ArgumentParser(description="Gera os assets do Ditto (alfa, fumaça e fundo sólido) sem fundo.jpg.")
    ap.add_argument("--ditto", help="imagem do Ditto (padrão: ditto.png/.gif/.webp da pasta)")
    ap.add_argument("--fumaca", help="vídeo/gif de fumaça (padrão: fumaca.* da pasta, senão gera uma)")
    ap.add_argument("--saida", default=d.saida, help="pasta de saída (padrão: ./saida)")
    ap.add_argument("--resolucao", default=f"{d.largura}x{d.altura}", help="LxA do quadro (padrão 1280x720)")
    ap.add_argument("--fps", type=int, default=d.fps)
    ap.add_argument("--escala", type=float, default=d.escala, help="altura do Ditto / altura do quadro (0.62)")
    ap.add_argument("--pos-x", type=float, default=d.pos_x, help="centro horizontal do Ditto (0..1)")
    ap.add_argument("--base-y", type=float, default=d.base_y, help="base do Ditto (0..1, 1 = rodapé)")
    ap.add_argument("--respiracao", type=float, default=d.respiracao, help="intensidade da respiração (0.04)")
    ap.add_argument("--balanco", type=float, default=d.balanco, help="balanço em graus (2.0)")
    ap.add_argument("--periodo", type=float, default=d.periodo, help="segundos por respiração (2.0)")
    ap.add_argument("--remover-fundo", choices=["auto", "sim", "nao"], default=d.remover_fundo,
                    help="recortar fundo liso do Ditto (auto = só se a imagem não tiver alfa)")
    ap.add_argument("--tolerancia", type=int, default=d.tolerancia, help="tolerância do recorte (40)")
    ap.add_argument("--fumaca-chave", choices=["auto", "preto", "verde", "nenhuma"], default=d.fumaca_chave)
    ap.add_argument("--fumaca-duracao", type=float, default=d.fumaca_duracao, help="duração da fumaça gerada (s)")
    ap.add_argument("--bg-cor", "--fundo-cor", dest="bg_cor", default=None,
                    help="gera também ditto_solido.mp4 sobre essa cor (ex.: '#1e1e2e'); 'transparente' = não gera")
    ap.add_argument("--mov", action="store_true", help="gera também ditto_overlay.mov (ProRes 4444 com alfa)")
    return ap


def opcoes_de_args(a: argparse.Namespace) -> Opcoes:
    try:
        w, h = (int(v) for v in a.resolucao.lower().split("x"))
    except ValueError:
        raise SystemExit("--resolucao deve ser no formato 1280x720")
    return Opcoes(
        ditto=a.ditto, fumaca=a.fumaca, saida=a.saida, largura=w - w % 2, altura=h - h % 2, fps=a.fps,
        escala=a.escala, pos_x=a.pos_x, base_y=a.base_y, respiracao=a.respiracao, balanco=a.balanco,
        periodo=a.periodo, remover_fundo=a.remover_fundo, tolerancia=a.tolerancia,
        fumaca_chave=a.fumaca_chave, fumaca_duracao=a.fumaca_duracao, bg_cor=a.bg_cor, mov=a.mov,
    )


def main(argv: list[str] | None = None) -> None:
    a = criar_parser().parse_args(argv)
    if a.bg_cor:
        parse_cor(a.bg_cor)  # valida cedo
    gerar(opcoes_de_args(a))


if __name__ == "__main__":
    main(sys.argv[1:])
