# Ditto no Teams — Kawarimi no Jutsu 🥷💨🟣

Aperte **F9** durante a reunião: uma nuvem de fumaça ninja cobre a câmera e, quando ela some, no seu lugar aparece um **Ditto** balançando. Aperte **F9** de novo e você volta.

Você **não precisa de foto de fundo** (`fundo.jpg`). O cenário é capturado **ao vivo, na hora do disparo**, então funciona em qualquer lugar.

---

## 1. Instalação (uma vez)

Pré-requisitos: **Python 3.10+** e **OBS Studio 28+** (já vem com o obs-websocket v5).
Não precisa instalar o ffmpeg: o pacote `imageio-ffmpeg` já traz um.

```powershell
py -m venv .venv
```
```powershell
.\.venv\Scripts\Activate.ps1
```
```powershell
pip install -r requirements.txt
```

## 2. Coloque os arquivos na pasta

| Arquivo | Obrigatório? | Observação |
|---|---|---|
| `ditto.png` / `ditto.gif` / `ditto.webp` | **sim** | Pode ter fundo branco/liso: ele é recortado automaticamente. Se for GIF animado, a animação é mantida. |
| `fumaca.mp4` / `fumaca.gif` / `fumaca.webm` | não | Fumaça sobre fundo **preto** ou **verde** é recortada sozinha. Sem esse arquivo, uma fumaça é gerada. |

Na primeira execução os assets são gerados automaticamente em `saida/`. Para gerar ou ajustar à mão:

```powershell
python preparar_ditto.py
```

Isso gera:
- `ditto_overlay.webm`: Ditto com **canal alfa**, com respiração e balanço em loop (modo **a**). Use `--mov` para gerar também uma versão ProRes 4444.
- `fumaca_alpha.webm`: a fumaça com alfa.
- `ditto_solido.mp4`: só com `--bg-cor "#1e1e2e"`. É um teste rápido sobre cor sólida (modo **c**).
- `ditto_preview.png`: um quadro para conferir o recorte.

Ajustes úteis: `--escala 0.7` (Ditto maior), `--pos-x 0.4`, `--respiracao 0.06`, `--balanco 3`, `--resolucao 1920x1080`, `--remover-fundo nao`, `--tolerancia 25`.

## 3. Teste sem OBS (opcional)

```powershell
python ditto_kawarimi.py --preview
```
Abre uma janela com a webcam. **Espaço/F9** alterna, **C/F8** memoriza o cenário limpo, **M/F10** troca o modo e **Q** sai.
Sem câmera: `python ditto_kawarimi.py --preview --sem-camera --bg-cor "#1e1e2e"`.

## 4. OBS

1. Abra o OBS e vá em **Ferramentas → Configurações do Servidor WebSocket**. Marque **Ativar servidor WebSocket** (porta 4455) e copie a senha.
2. Rode o setup. Ele cria as cenas e as fontes que faltarem e fica ouvindo o F9:

```powershell
python ditto_kawarimi.py --setup --senha SUA_SENHA --vcam
```

O setup cria ou verifica:

| Cena | Fontes (de baixo para cima) |
|---|---|
| `Webcam_Normal` | `Camera` (sua webcam), `Fumaca` |
| `Modo_Ditto` | `Ditto_Cor`, `Camera`, `Ditto_Fundo` (cenário congelado), `Ditto_Overlay` (Ditto com alfa), `Fumaca` |

Se a câmera errada aparecer, dê dois cliques em `Camera` no OBS e escolha o dispositivo.
Nas próximas vezes basta rodar `python ditto_kawarimi.py --senha SUA_SENHA` (ou definir `$env:OBS_WS_SENHA`). Outro host ou porta: `--host 192.168.0.10 --porta 4455`.

### Os três modos (`--modo`, ou **F10** para alternar)

| Modo | O que aparece atrás do Ditto | Quando usar |
|---|---|---|
| `ao-vivo` (padrão) | o **cenário real congelado** no instante do F9 | em qualquer lugar, sem preparar nada |
| `alpha` | a **câmera ao vivo** (Ditto sobreposto com alfa) | quando você quer continuar aparecendo |
| `solido` | uma **cor sólida** (`--bg-cor "#1e1e2e"`) | para testar rápido ou com a câmera fechada |

**Truque do F8 (cenário limpo):** no modo ao-vivo, a imagem congelada tem você nela (por isso ela é levemente desfocada, veja `--desfoque`). Para o Ditto "ocupar o seu lugar" de verdade, saia do quadro por um segundo e aperte **F8**. Daí em diante, o F9 usa esse fundo sem você. Vale até fechar o script. Aperte F8 de novo se mudar de lugar.

## 5. Teams

1. No OBS, clique em **Iniciar câmera virtual** (ou use `--vcam`).
2. No Teams, vá em **Configurações → Dispositivos → Câmera** e escolha **OBS Virtual Camera**.
3. Deixe o `ditto_kawarimi.py` rodando no terminal. O F9 funciona mesmo com o Teams em foco.

> O Teams espelha só a *sua* pré-visualização. Os outros participantes veem a imagem normal.

## Teclas

| Tecla | Ação |
|---|---|
| **F9** | Kawarimi: alterna `Webcam_Normal` ↔ `Modo_Ditto` com fumaça |
| **F8** | memoriza o cenário limpo (saia do quadro antes) |
| **F10** | troca o modo: ao-vivo → alpha → solido |
| **Ctrl+C** | encerra o script |

As teclas podem ser trocadas com `--tecla F7`, `--tecla-cenario F6` e `--tecla-modo F11`.

## Problemas comuns

- **"Não consegui conectar ao OBS"**: o OBS precisa estar aberto com o WebSocket ativo. Confira porta e senha.
- **A fumaça não aparece**: rode `--setup` de novo. A fonte `Fumaca` precisa estar no topo das duas cenas.
- **Halo branco no Ditto**: `python preparar_ditto.py --tolerancia 60`. Se a imagem já tiver transparência: `--remover-fundo nao`.
- **Troca antes ou depois do pico da fumaça**: ajuste com `--troca-em 0.6` (em segundos).
- **Webcam ocupada no `--preview`**: o OBS ou o Teams estão usando a câmera. Feche-os ou use `--sem-camera`.
