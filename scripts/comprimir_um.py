"""Fase 3: comprime UM PDF com uma receita e mede tamanho, tempo e memória.

Receitas:
  1 = sem perda: remove cópias de objetos (dedup), recomprime e agrupa objetos.
  2 = receita 1 + reduz imagens acima do limite de DPI e recomprime em JPEG
      (imagens que contêm código de barras ou QR Code são preservadas).

Uso:
  python scripts\\comprimir_um.py "S:\\...\\arquivo.pdf" --receita 1
  python scripts\\comprimir_um.py "S:\\...\\arquivo.pdf" --receita 2 --dpi 150
  (opcional) --referencia "S:\\...\\Comprimido\\arquivo-compressed.pdf"

O original NUNCA é alterado: o resultado vai para data\\saida\\.
Cada execução fica registrada no banco de auditoria (tabelas execucao e arquivo).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import psutil
import pymupdf
import zxingcpp
from sqlalchemy.orm import Session

import config
import db  # scripts/db.py (Fase 5)

MB = 1024 * 1024
FORMATOS_PROTEGIDOS = (zxingcpp.BarcodeFormat.ITF, zxingcpp.BarcodeFormat.QRCode)


def pico_memoria_mb() -> float:
    """Maior uso de memória do processo até agora (inclui a parte em C do MuPDF)."""
    info = psutil.Process().memory_info()
    if hasattr(info, "peak_wset"):  # Windows
        return info.peak_wset / MB
    import resource  # Linux/macOS: ru_maxrss em KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def etapa(nome: str, inicio: float) -> float:
    agora = time.perf_counter()
    print(f"  {nome:<44} {agora - inicio:6.1f} s   (pico de memória {pico_memoria_mb():,.0f} MB)")
    return agora


def deduplicar(origem: Path) -> bytes:
    """Receita 1: grava o PDF em memória removendo cópias e recomprimindo.

    garbage=4  -> remove objetos sem uso E junta objetos de conteúdo idêntico
                  (é aqui que as ~6.000 cópias de imagem viram ~18)
    deflate    -> comprime com Flate (sem perda) o que estiver sem compressão
    use_objstms-> agrupa os objetos pequenos (dicionários) em streams comprimidos
    Não usamos clean=True: ele reescreve os desenhos das páginas, e os códigos
    de barras devem continuar exatamente como estão.
    """
    with pymupdf.open(origem) as doc:
        return doc.tobytes(garbage=4, deflate=True, deflate_images=True,
                           deflate_fonts=True, use_objstms=1)


def tem_codigo(pix: pymupdf.Pixmap) -> bool:
    """A imagem contém um código de barras (ITF) ou QR Code legível?

    Alguns geradores gravam o código como IMAGEM (e não como vetor). Reduzir essa
    imagem para 150 DPI em JPEG afina e borra as barras: o software ainda lê,
    mas leitoras de banco e apps de celular podem não ler. Por isso essas
    imagens nunca são reduzidas.
    """
    cinza = pix if pix.n == 1 else pymupdf.Pixmap(pymupdf.csGRAY, pix)
    matriz = np.frombuffer(cinza.samples, dtype=np.uint8).reshape(cinza.height, cinza.stride)[:, :cinza.width]
    return bool(zxingcpp.read_barcodes(matriz, formats=FORMATOS_PROTEGIDOS))


def reduzir_imagens(pdf: bytes, dpi: int, qualidade: int) -> tuple[bytes, int, int]:
    """Receita 2 (com perda): reduz cada imagem DISTINTA acima de `dpi` para `dpi`, em JPEG.

    Roda DEPOIS da deduplicação: cada imagem é tratada UMA vez e a troca vale
    para todas as páginas que a usam. (Não usamos o rewrite_images do PyMuPDF:
    em teste ele reduziu o mesmo logo várias vezes em cascata, até 4x2 pixels.)
    Imagens que CONTÊM código de barras ou QR Code são preservadas intactas.
    Devolve o PDF, quantas imagens foram reduzidas e quantas foram protegidas.
    """
    with pymupdf.open("pdf", pdf) as doc:
        # 1) Maior tamanho em que cada imagem aparece impressa (lado maior, em polegadas).
        #    Usar o MAIOR tamanho garante que nenhuma ocorrência fique abaixo do DPI alvo.
        maior_lado: dict[int, float] = {}
        for pagina in doc:
            for uso in pagina.get_image_info(xrefs=True):
                xref = uso["xref"]
                caixa = pymupdf.Rect(uso["bbox"])
                lado = max(caixa.width, caixa.height) / 72
                if xref > 0 and lado > maior_lado.get(xref, 0):
                    maior_lado[xref] = lado

        # 2) Reduz cada imagem distinta que está acima do alvo.
        reduzidas = protegidas = 0
        for xref, lado in maior_lado.items():
            largura = int(doc.xref_get_key(xref, "Width")[1])
            altura = int(doc.xref_get_key(xref, "Height")[1])
            if (doc.xref_get_key(xref, "ImageMask")[1] == "true"        # máscara 1 bit
                    or doc.xref_get_key(xref, "BitsPerComponent")[1] == "1"
                    or max(largura, altura) < 16):                        # faixas 1x1 etc.
                continue
            dpi_atual = max(largura, altura) / lado
            if dpi_atual <= dpi * 1.1:                                    # já está no alvo
                continue
            escala = dpi / dpi_atual
            pix = pymupdf.Pixmap(doc, xref)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)                              # JPEG não tem transparência
            if pix.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)                  # CMYK etc. -> RGB
            if tem_codigo(pix):                                           # código em imagem: não mexe
                protegidas += 1
                continue
            menor = pymupdf.Pixmap(pix, max(1, round(pix.width * escala)),
                                   max(1, round(pix.height * escala)), None)
            # Troca o conteúdo NO PRÓPRIO objeto: todas as páginas que apontam
            # para ele passam a usar a versão reduzida. A máscara (/SMask), se
            # houver, é preservada porque não mexemos nessa chave.
            doc.update_stream(xref, menor.tobytes("jpg", jpg_quality=qualidade), compress=False)
            doc.xref_set_key(xref, "Filter", "/DCTDecode")
            doc.xref_set_key(xref, "DecodeParms", "null")
            doc.xref_set_key(xref, "Decode", "null")
            doc.xref_set_key(xref, "Width", str(menor.width))
            doc.xref_set_key(xref, "Height", str(menor.height))
            doc.xref_set_key(xref, "ColorSpace", "/DeviceGray" if menor.n == 1 else "/DeviceRGB")
            doc.xref_set_key(xref, "BitsPerComponent", "8")
            reduzidas += 1
        return doc.tobytes(garbage=4, deflate=True, use_objstms=1), reduzidas, protegidas


# 49010000_49027415_1 -> CEP inicial, CEP final, número do lote
RE_NOME_LOTE = re.compile(r"^(?P<cep_inicio>\d{8})_(?P<cep_fim>\d{8})_(?P<lote>\d+)$")


def sha256_arquivo(caminho: Path) -> str:
    """'Impressão digital' do arquivo, lida em blocos de 1 MB (não carrega tudo na memória)."""
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        while bloco := f.read(1024 * 1024):
            h.update(bloco)
    return h.hexdigest()


def metadados(caminho: Path) -> dict:
    """CDD = nome da pasta; CEPs e lote = nome do arquivo (quando seguir o padrão)."""
    m = RE_NOME_LOTE.match(caminho.stem)
    return {
        "cdd": caminho.parent.name or None,
        "cep_inicio": m["cep_inicio"] if m else None,
        "cep_fim": m["cep_fim"] if m else None,
        "lote": int(m["lote"]) if m else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fase 3: comprime um PDF e mede")
    ap.add_argument("arquivo", type=Path)
    cfg = config.carregar()   # padrões vêm do config.toml; a linha de comando sobrescreve
    ap.add_argument("--receita", type=int, choices=(1, 2), default=cfg["compressao"]["receita"])
    ap.add_argument("--dpi", type=int, default=cfg["compressao"]["dpi"],
                    help="receita 2: DPI alvo das imagens")
    ap.add_argument("--qualidade", type=int, default=cfg["compressao"]["qualidade_jpeg"],
                    help="receita 2: qualidade JPEG (0-100)")
    ap.add_argument("--referencia", type=Path, help="arquivo do PDF24 para comparar")
    # Uso manual: grava em trabalho\ (NÃO em saida\, que é só para arquivos já validados)
    ap.add_argument("--saida", type=Path, default=config.caminho(cfg["pastas"]["dados"]) / "trabalho")
    a = ap.parse_args()

    sufixo = f"-r1" if a.receita == 1 else f"-r2-{a.dpi}dpi-q{a.qualidade}"
    destino = a.saida / f"{a.arquivo.stem}{sufixo}.pdf"
    a.saida.mkdir(parents=True, exist_ok=True)

    # 1) Registra a execução e o arquivo ANTES de processar, com status PROCESSANDO.
    #    Se o processo cair no meio, o banco mostra o que ficou pela metade.
    engine = db.conectar()
    with Session(engine) as s:
        lote_id = int(os.environ["LOTE_ID"]) if os.environ.get("LOTE_ID") else None  # vem do processar_lote
        execucao = db.Execucao(lote_id=lote_id, versao_codigo=db.versao_codigo(), receita=a.receita,
                               dpi=a.dpi if a.receita == 2 else None,
                               qualidade_jpeg=a.qualidade if a.receita == 2 else None)
        registro = db.Arquivo(execucao=execucao, nome=a.arquivo.name, **metadados(a.arquivo),
                              bytes_original=a.arquivo.stat().st_size,
                              sha256_original=sha256_arquivo(a.arquivo),
                              bytes_pdf24=a.referencia.stat().st_size if a.referencia else None,
                              status="PROCESSANDO")
        s.add(execucao)
        s.commit()
        execucao_id, arquivo_id = execucao.id, registro.id

    print(f"\nArquivo : {a.arquivo.name} ({a.arquivo.stat().st_size / MB:,.1f} MB)")
    print(f"Receita : {a.receita}" + (f" (DPI {a.dpi}, JPEG q{a.qualidade})" if a.receita == 2 else " (sem perda)"))
    print("Etapas:")
    t0 = t = time.perf_counter()
    resultado: dict = {}

    # 2) Processa. Qualquer erro vira status ERRO no banco, em vez de sumir no terminal.
    #    Gravação ATÔMICA: grava primeiro num arquivo .tmp e só renomeia para o nome final
    #    quando estiver completo e conferido. Assim nunca fica um PDF pela metade com nome
    #    de pronto (se o processo for interrompido, sobra no máximo o .tmp, que é apagado).
    temporario = destino.with_name(destino.name + ".tmp")
    try:
        pdf = deduplicar(a.arquivo)
        t = etapa("1. remover cópias e recomprimir (sem perda)", t)
        if a.receita == 2:
            pdf, reduzidas, protegidas = reduzir_imagens(pdf, a.dpi, a.qualidade)
            t = etapa(f"2. reduzir {reduzidas} imagens para {a.dpi} DPI (JPEG)", t)
            if protegidas:
                print(f"     {protegidas} imagem(ns) com código de barras/QR preservada(s) sem alteração")

        temporario.write_bytes(pdf)

        # Conferência mínima (a validação completa é a Fase 4), ainda no temporário
        with pymupdf.open(a.arquivo) as orig, pymupdf.open(temporario) as novo:
            paginas, paginas_ok = len(orig), len(orig) == len(novo)

        if paginas_ok:
            resultado = dict(paginas=paginas, carnes=paginas // 2,
                             bytes_comprimido=temporario.stat().st_size,
                             sha256_comprimido=sha256_arquivo(temporario), status="OK")
            os.replace(temporario, destino)   # "publica": renomear é instantâneo
            t = etapa("3. gravar resultado", t)
        else:
            resultado = dict(paginas=paginas, status="ERRO", mensagem="número de páginas diferente")
    except Exception as erro:
        resultado = dict(status="ERRO", mensagem=f"{type(erro).__name__}: {erro}"[:4000])
        print(f"\nERRO: {resultado['mensagem']}")
    finally:
        temporario.unlink(missing_ok=True)   # vale também para Ctrl+C

    # 3) Atualiza o registro com o resultado (sucesso ou erro) e fecha a execução.
    resultado.update(tempo_s=round(time.perf_counter() - t0, 1),
                     pico_memoria_mb=round(pico_memoria_mb(), 0))
    with Session(engine) as s:
        registro = s.get(db.Arquivo, arquivo_id)
        for campo, valor in resultado.items():
            setattr(registro, campo, valor)
        s.get(db.Execucao, execucao_id).finalizada_em = db.agora()
        s.commit()

    if resultado["status"] == "OK":
        original, final = a.arquivo.stat().st_size, resultado["bytes_comprimido"]
        print("\nResultado:")
        print(f"  original          : {original / MB:8,.1f} MB")
        print(f"  comprimido        : {final / MB:8,.1f} MB   (-{100 * (1 - final / original):.1f}%)")
        if a.referencia:
            ref = a.referencia.stat().st_size
            dif = 100 * (final / ref - 1)
            print(f"  PDF24             : {ref / MB:8,.1f} MB   (nosso é {abs(dif):.1f}% "
                  f"{'MENOR' if dif < 0 else 'maior'})")
        print(f"  tempo total       : {resultado['tempo_s']:8,.1f} s")
        print(f"  pico de memória   : {resultado['pico_memoria_mb']:8,.0f} MB")
        print(f"  salvo em          : {destino}")
    print(f"\nRegistrado no banco: execução {execucao_id}, arquivo {arquivo_id}, status {resultado['status']}")
    return 0 if resultado["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
