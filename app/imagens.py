"""Ler e gravar imagem sem depender do OpenCV pra abrir o arquivo.

ARMADILHA MEDIDA (01/08/2026, OpenCV 4.11, Windows com codepage cp1252):
o OpenCV abre arquivo pela API ANSI do Windows. A string vem em UTF-8 dos
bindings, é reinterpretada na codepage do sistema, vira mojibake — e a
operação falha SEM LEVANTAR NADA:

    pasta ASCII .................  imwrite -> True, arquivo de 347 KB
                                   imread  -> array (64, 96, 3)
    pasta "…O Último Mestre…" ...  imwrite -> False, NENHUM arquivo criado
                                   imread  -> None, com o arquivo no disco
    pasta em japonês ............  as duas falham igual

`cv2.VideoCapture` NÃO tem esse problema (usa o backend do ffmpeg, que
aguenta unicode), e o ffmpeg por subprocess também não. É só o I/O de
imagem do OpenCV.

O QUE ISSO CUSTOU: um usuário analisou "Avatar Aang - O Último Mestre do Ar
2026". Os 1470 clipes saíram (ffmpeg), nenhum keyframe .jpg saiu (OpenCV),
e como o `keyframe_extractor` não conferia o retorno do `imwrite`, o banco
gravou 1470 caminhos de keyframe que nunca existiram. Sem os .jpg, os
`imread` da análise devolveram None: ZERO personagens identificados e a
grade inteira preta — com a prévia em vídeo tocando normal, porque quem
escreve o clipe é o ffmpeg. Nada disso apareceu na tela ou no log.

A saída é não deixar o OpenCV tocar no arquivo: o Python abre (o `open()`
dele usa a API wide do Windows e é unicode-safe), o OpenCV só decodifica ou
codifica os BYTES em memória. Custo medido: nenhum — `imdecode`/`imencode`
fazem o mesmo trabalho de decodificação, só que a partir de um buffer.
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np


def ler_imagem(caminho: str | Path, flags: int = cv2.IMREAD_COLOR):
    """Como `cv2.imread`: devolve o array BGR, ou None se não deu.

    Mesmo contrato do original — quem chama já trata None —, só que agora
    None quer dizer "o arquivo não existe ou não é imagem", e não "tem
    acento no caminho".
    """
    try:
        dados = Path(caminho).read_bytes()
    except OSError:
        return None
    if not dados:
        return None
    return cv2.imdecode(np.frombuffer(dados, np.uint8), flags)


def gravar_imagem(caminho: str | Path, imagem, params: list[int] | None = None) -> bool:
    """Como `cv2.imwrite`: True se gravou. CONFIRA O RETORNO.

    Grava em arquivo temporário e renomeia por cima. O `.jpg` pela metade
    (queda de energia, disco cheio no meio do lote) passaria pelo teste de
    `st_size > 0` que decide se o keyframe já existe, e a análise seguinte
    reaproveitaria um arquivo truncado achando que estava pronto.
    """
    p = Path(caminho)
    ext = p.suffix or ".jpg"
    try:
        ok, buf = cv2.imencode(ext, imagem, params or [])
    except cv2.error:
        return False
    if not ok:
        return False
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(buf.tobytes())
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True
