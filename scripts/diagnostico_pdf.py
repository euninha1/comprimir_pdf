"""Diagnóstico SOMENTE LEITURA de um PDF: o que ocupa espaço e por quê.

Uso:
    python scripts\\diagnostico_pdf.py "S:\\Modelo_IPTU\\...\\arquivo.pdf"

Nada é gravado: o arquivo é aberto só para leitura.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

MB = 1024 * 1024
TAMANHOS_PAPEL = {"A4": (595, 842), "A3": (842, 1191)}  # em pontos (1 pt = 1/72 pol)


def nome_papel(largura: float, altura: float) -> str:
    menor, maior = sorted((largura, altura))
    for nome, (w, h) in TAMANHOS_PAPEL.items():
        if abs(menor - w) < 5 and abs(maior - h) < 5:
            return f"{nome} {'paisagem' if largura > altura else 'retrato'}"
    return "fora do padrão A3/A4"


def tamanho_stream(doc: pymupdf.Document, xref: int) -> int:
    """Bytes gravados do stream (comprimido, como está no disco)."""
    tipo, valor = doc.xref_get_key(xref, "Length")
    if tipo == "int":
        return int(valor)
    if tipo == "xref":  # /Length 12 0 R -> o tamanho está em outro objeto
        return int(doc.xref_object(int(valor.split()[0])).strip())
    return 0


def espaco_de_cor(doc: pymupdf.Document, xref: int) -> str:
    """RGB, Gray, CMYK... resolvendo referências indiretas (/ColorSpace 7 0 R)."""
    tipo, valor = doc.xref_get_key(xref, "ColorSpace")
    if tipo == "xref":
        valor = doc.xref_object(int(valor.split()[0]), compressed=True)
    if tipo == "null" and doc.xref_get_key(xref, "ImageMask")[1] == "true":
        return "Mask"
    icc = re.search(r"/ICCBased\s+(\d+)\s+0\s+R", valor)
    if icc:  # perfil ICC: a cor é dada pelo nº de canais (/N) do objeto do perfil
        canais = doc.xref_get_key(int(icc.group(1)), "N")[1]
        return {"1": "Gray", "3": "RGB", "4": "CMYK"}.get(canais, "ICC")
    for nome in ("RGB", "Gray", "CMYK", "Indexed", "Separation", "Lab"):
        if nome in valor:
            return nome
    return valor[:5] or "?"


def categoria(doc: pymupdf.Document, xref: int) -> str:
    subtipo = doc.xref_get_key(xref, "Subtype")[1]
    tipo = doc.xref_get_key(xref, "Type")[1]
    if subtipo == "/Image":
        return "imagens"
    if subtipo == "/Form":
        return "form xobjects"
    if tipo in ("/XRef", "/ObjStm"):
        return "estrutura do PDF"
    if any(doc.xref_get_key(xref, k)[0] != "null" for k in ("Length1", "Length2", "Length3")) \
            or subtipo in ("/Type1C", "/CIDFontType0C", "/OpenType"):
        return "fontes"
    return "conteúdo das páginas e outros"


def main(caminho: Path) -> None:
    doc = pymupdf.open(caminho)  # leitura apenas; nunca chamamos save()
    tamanho = caminho.stat().st_size
    pagina = doc[0].rect

    print(f"\nArquivo : {caminho.name}")
    print(f"Tamanho : {tamanho / MB:,.1f} MB")
    print(f"Páginas : {len(doc)}  (≈ {len(doc) // 2} carnês de 2 páginas)")
    print(f"Página  : {pagina.width:.0f} x {pagina.height:.0f} pt "
          f"= {pagina.width / 72 * 25.4:.0f} x {pagina.height / 72 * 25.4:.0f} mm "
          f"-> {nome_papel(pagina.width, pagina.height)}")

    # 1) Onde está o peso: soma dos streams por categoria
    por_categoria: Counter[str] = Counter()
    for xref in range(1, doc.xref_length()):
        if doc.xref_is_stream(xref):
            por_categoria[categoria(doc, xref)] += tamanho_stream(doc, xref)
    por_categoria["dicionários e índice (sem stream)"] = max(0, tamanho - sum(por_categoria.values()))
    print("\n== Onde está o peso ==")
    for cat, total in por_categoria.most_common():
        print(f"  {cat:<36} {total / MB:>9,.1f} MB  ({100 * total / tamanho:5.1f}%)")

    # 2) Imagens: objetos x conteúdos distintos (potencial de deduplicação)
    usos: Counter[int] = Counter()
    for p in doc:
        for img in p.get_images(full=True):
            usos[img[0]] += 1
    grupos: dict[str, list[int]] = defaultdict(list)  # hash do conteúdo -> xrefs
    for xref in usos:
        grupos[hashlib.md5(doc.xref_stream_raw(xref)).hexdigest()].append(xref)
    bytes_imagens = sum(tamanho_stream(doc, x) for x in usos)
    bytes_unicos = sum(tamanho_stream(doc, xs[0]) for xs in grupos.values())
    print("\n== Imagens ==")
    print(f"  usos de imagem nas páginas   : {sum(usos.values()):,}")
    print(f"  objetos de imagem no arquivo : {len(usos):,}")
    print(f"  conteúdos distintos          : {len(grupos):,}")
    print(f"  peso atual das imagens       : {bytes_imagens / MB:,.1f} MB")
    print(f"  peso sem cópias repetidas    : {bytes_unicos / MB:,.1f} MB"
          f"  <- alvo da deduplicação (SEM perda de qualidade)")

    # 3) Detalhe das imagens distintas mais pesadas
    print("\n== Imagens distintas mais pesadas ==")
    print(f"  {'cópias':>6} {'KB cada':>8} {'pixels':>11} {'cor':>5} {'bits':>4}  formato")
    print("  (cópias = objetos com conteúdo idêntico; KB = tamanho gravado de UMA cópia)")
    filtros = {"/DCTDecode": "JPEG", "/FlateDecode": "Flate (sem perda)",
               "/CCITTFaxDecode": "CCITT (1 bit)", "/JBIG2Decode": "JBIG2 (1 bit)",
               "/JPXDecode": "JPEG 2000"}
    ordenados = sorted(grupos.values(), key=lambda xs: tamanho_stream(doc, xs[0]), reverse=True)
    for xs in ordenados[:10]:
        x = xs[0]
        w = doc.xref_get_key(x, "Width")[1]
        h = doc.xref_get_key(x, "Height")[1]
        cs = espaco_de_cor(doc, x)
        bpc = doc.xref_get_key(x, "BitsPerComponent")[1]
        filtro = doc.xref_get_key(x, "Filter")[1]
        formato = "sem compressão" if filtro == "null" else filtros.get(filtro, filtro)
        print(f"  {len(xs):>6} {tamanho_stream(doc, x) / 1024:>8,.0f} {w + 'x' + h:>11} "
              f"{cs:>5} {bpc:>4}  {formato}")

    # 5) Form XObjects: blocos de desenho reaproveitáveis ("carimbos")
    forms: dict[str, list[int]] = defaultdict(list)   # hash do conteúdo -> xrefs
    por_filtro: Counter[str] = Counter()
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Subtype")[1] == "/Form":
            forms[hashlib.md5(doc.xref_stream_raw(xref)).hexdigest()].append(xref)
            filtro = doc.xref_get_key(xref, "Filter")[1]
            por_filtro["sem compressão" if filtro == "null" else filtro] += 1
    total_forms = sum(len(xs) for xs in forms.values())
    bytes_forms = sum(tamanho_stream(doc, x) for xs in forms.values() for x in xs)
    bytes_unicos_forms = sum(tamanho_stream(doc, xs[0]) for xs in forms.values())
    print("\n== Form XObjects (blocos de desenho reaproveitáveis) ==")
    print(f"  objetos no arquivo  : {total_forms:,}")
    print(f"  conteúdos distintos : {len(forms):,}")
    print(f"  peso atual          : {bytes_forms / MB:,.1f} MB")
    print(f"  peso sem cópias     : {bytes_unicos_forms / MB:,.1f} MB")
    print(f"  compressão          : {dict(por_filtro)}")
    print("  mais pesados (cópias x KB de cada uma):")
    pesados = sorted(forms.values(), key=lambda xs: len(xs) * tamanho_stream(doc, xs[0]), reverse=True)
    for xs in pesados[:5]:
        print(f"    {len(xs):>6} x {tamanho_stream(doc, xs[0]) / 1024:>7,.1f} KB")

    # 4) Resolução efetiva e vetores nas 2 primeiras páginas (1 carnê: frente e verso)
    for numero in range(min(2, len(doc))):
        p = doc[numero]
        print(f"\n== Página {numero + 1} ({'frente' if numero == 0 else 'verso'}) ==")
        for im in p.get_image_info(xrefs=True):
            caixa = pymupdf.Rect(im["bbox"])
            if caixa.width <= 0:
                continue
            # max/max: a imagem pode estar girada 90° na página
            dpi = max(im["width"], im["height"]) / (max(caixa.width, caixa.height) / 72)
            print(f"  imagem {im['width']}x{im['height']} px em {caixa.width / 72 * 25.4:.0f}x"
                  f"{caixa.height / 72 * 25.4:.0f} mm -> {dpi:,.0f} DPI")
        desenhos = p.get_drawings()
        print(f"  desenhos vetoriais (barras, linhas, caixas): {len(desenhos):,}")
        print(f"  trechos de texto: {len(p.get_text('blocks')):,}")

    doc.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: python diagnostico_pdf.py <arquivo.pdf>")
    main(Path(sys.argv[1]))
