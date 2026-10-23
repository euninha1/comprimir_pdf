"""Fase 3: comprime UM PDF com uma receita e mede tamanho, tempo e memória.

Receitas:
  1 = sem perda: remove cópias de objetos (dedup), recomprime e agrupa objetos.
  2 = receita 1 + reduz imagens acima do limite de DPI e recomprime em JPEG.

Uso:
  python scripts\\comprimir_um.py "S:\\...\\arquivo.pdf" --receita 1
  python scripts\\comprimir_um.py "S:\\...\\arquivo.pdf" --receita 2 --dpi 150
  (opcional) --referencia "S:\\...\\Comprimido\\arquivo-compressed.pdf"

O original NUNCA é alterado: o resultado vai para data\\saida\\.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import psutil
import pymupdf

MB = 1024 * 1024


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


def reduzir_imagens(pdf: bytes, dpi: int, qualidade: int) -> tuple[bytes, int]:
    """Receita 2 (com perda): reduz cada imagem DISTINTA acima de `dpi` para `dpi`, em JPEG.

    Roda DEPOIS da deduplicação: cada imagem é tratada UMA vez e a troca vale
    para todas as páginas que a usam. (Não usamos o rewrite_images do PyMuPDF:
    em teste ele reduziu o mesmo logo várias vezes em cascata, até 4x2 pixels.)
    Códigos de barras e QR são vetores, então não são afetados.
    Devolve o PDF e quantas imagens foram reduzidas.
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
        reduzidas = 0
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
        return doc.tobytes(garbage=4, deflate=True, use_objstms=1), reduzidas


def main() -> None:
    ap = argparse.ArgumentParser(description="Fase 3: comprime um PDF e mede")
    ap.add_argument("arquivo", type=Path)
    ap.add_argument("--receita", type=int, choices=(1, 2), default=1)
    ap.add_argument("--dpi", type=int, default=150, help="receita 2: DPI alvo das imagens")
    ap.add_argument("--qualidade", type=int, default=80, help="receita 2: qualidade JPEG (0-100)")
    ap.add_argument("--referencia", type=Path, help="arquivo do PDF24 para comparar")
    ap.add_argument("--saida", type=Path, default=Path("data/saida"))
    a = ap.parse_args()

    sufixo = f"-r1" if a.receita == 1 else f"-r2-{a.dpi}dpi-q{a.qualidade}"
    destino = a.saida / f"{a.arquivo.stem}{sufixo}.pdf"
    a.saida.mkdir(parents=True, exist_ok=True)

    print(f"\nArquivo : {a.arquivo.name} ({a.arquivo.stat().st_size / MB:,.1f} MB)")
    print(f"Receita : {a.receita}" + (f" (DPI {a.dpi}, JPEG q{a.qualidade})" if a.receita == 2 else " (sem perda)"))
    print("Etapas:")
    t0 = t = time.perf_counter()

    pdf = deduplicar(a.arquivo)
    t = etapa("1. remover cópias e recomprimir (sem perda)", t)
    if a.receita == 2:
        pdf, reduzidas = reduzir_imagens(pdf, a.dpi, a.qualidade)
        t = etapa(f"2. reduzir {reduzidas} imagens para {a.dpi} DPI (JPEG)", t)

    destino.write_bytes(pdf)
    t = etapa("3. gravar resultado", t)

    # Conferência mínima (a validação completa é a Fase 4)
    with pymupdf.open(a.arquivo) as orig, pymupdf.open(destino) as novo:
        paginas_ok = len(orig) == len(novo)

    original = a.arquivo.stat().st_size
    final = destino.stat().st_size
    print("\nResultado:")
    print(f"  original          : {original / MB:8,.1f} MB")
    print(f"  comprimido        : {final / MB:8,.1f} MB   (-{100 * (1 - final / original):.1f}%)")
    if a.referencia:
        ref = a.referencia.stat().st_size
        dif = 100 * (final / ref - 1)
        print(f"  PDF24             : {ref / MB:8,.1f} MB   (nosso é {abs(dif):.1f}% "
              f"{'MENOR' if dif < 0 else 'maior'})")
    print(f"  tempo total       : {time.perf_counter() - t0:8,.1f} s")
    print(f"  pico de memória   : {pico_memoria_mb():8,.0f} MB")
    print(f"  páginas iguais    : {'sim' if paginas_ok else 'NÃO  <-- verificar!'}")
    print(f"  salvo em          : {destino}")


if __name__ == "__main__":
    main()
