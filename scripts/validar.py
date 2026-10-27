"""Fase 4: valida se o PDF comprimido é equivalente ao original, carnê por carnê.

Verificações (página a página):
  1. mesmo número de páginas
  2. texto idêntico
  3. linhas digitáveis idênticas
  4. regra do carnê no original: frente com 1 linha digitável, verso com 1 a 12
  5. códigos de barras (ITF) lidos por software: iguais no original e no comprimido
     e iguais ao número da linha digitável impressa
  6. QR Codes lidos por software: iguais no original e no comprimido

Uso:
  python scripts\\validar.py "S:\\...\\original.pdf" "data\\saida\\comprimido.pdf"
  opções: --amostra 10  (lê códigos em 1 de cada 10 carnês; padrão 1 = todos)
          --dpi 200     (resolução usada para "fotografar" a página para leitura)

Não imprime dados de contribuintes: só números de página e o tipo do problema.
Sai com código 0 se aprovado e 1 se reprovado (útil para automação).

Banco (Fase 5): o resultado é gravado nas tabelas validacao e problema, ligado ao
registro da tabela arquivo cujo sha256_comprimido é igual ao do PDF validado.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pymupdf
import zxingcpp
from sqlalchemy import select
from sqlalchemy.orm import Session

import db  # scripts/db.py (Fase 5)
from comprimir_um import sha256_arquivo

# Linha digitável: 47 dígitos (boleto) ou 48 (arrecadação/prefeituras), com espaços
# ou pontos entre os blocos, sem atravessar quebra de linha.
RE_LINHA = re.compile(r"(?<!\d)(?:\d[ .\-]{0,3}){46,47}\d(?!\d)")
FORMATOS = (zxingcpp.BarcodeFormat.ITF, zxingcpp.BarcodeFormat.QRCode)
MAX_PARCELAS = 12


def linhas_digitaveis(texto: str) -> list[str]:
    return [re.sub(r"\D", "", m) for m in RE_LINHA.findall(texto)]


def codigo_da_linha(linha: str) -> str | None:
    if len(linha) != 48:
        return None
    return "".join(linha[i:i + 11] for i in range(0, 48, 12))


def ler_codigos(pagina: pymupdf.Page, dpi: int) -> Counter[tuple[str, str]]:
    pix = pagina.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    imagem = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    lidos = zxingcpp.read_barcodes(imagem, formats=FORMATOS)
    return Counter(("QR" if r.format == zxingcpp.BarcodeFormat.QRCode else "ITF", r.text) for r in lidos)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fase 4: valida original x comprimido")
    ap.add_argument("original", type=Path)
    ap.add_argument("comprimido", type=Path)
    ap.add_argument("--amostra", type=int, default=1, help="ler códigos em 1 a cada N carnês")
    ap.add_argument("--dpi", type=int, default=200, help="resolução da leitura dos códigos")
    a = ap.parse_args()

    t0 = time.perf_counter()
    orig, comp = pymupdf.open(a.original), pymupdf.open(a.comprimido)
    problemas: list[tuple[int | None, int | None, str | None, str, str]] = []
    cont = Counter()

    print(f"\nOriginal   : {a.original.name} ({len(orig)} páginas)")
    print(f"Comprimido : {a.comprimido.name} ({len(comp)} páginas)")
    print(f"Leitura de códigos: {'todos os carnês' if a.amostra == 1 else f'1 a cada {a.amostra} carnês'}"
          f", a {a.dpi} DPI\n")

    if len(orig) != len(comp):
        problemas.append((None, None, None, "PAGINAS", f"número de páginas diferente: {len(orig)} x {len(comp)}"))
    if len(orig) % 2:
        problemas.append((None, None, None, "PAGINAS", f"original com número ímpar de páginas ({len(orig)}): carnê incompleto?"))

    for n in range(min(len(orig), len(comp))):
        po, pc = orig[n], comp[n]
        carne, frente = n // 2 + 1, n % 2 == 0
        lado = "frente" if frente else "verso"
        texto_o, texto_c = po.get_text(), pc.get_text()

        cont["páginas comparadas"] += 1
        if " ".join(texto_o.split()) != " ".join(texto_c.split()):
            problemas.append((n + 1, carne, lado, "TEXTO", "texto diferente"))

        lin_o, lin_c = linhas_digitaveis(texto_o), linhas_digitaveis(texto_c)
        cont["linhas digitáveis"] += len(lin_o)
        if lin_o != lin_c:
            problemas.append((n + 1, carne, lado, "LINHA_DIGITAVEL", "linhas digitáveis diferentes"))

        if frente and len(lin_o) != 1:
            problemas.append((n + 1, carne, lado, "REGRA_CARNE", f"{len(lin_o)} linhas digitáveis na frente (esperado 1)"))
        if not frente and not 1 <= len(lin_o) <= MAX_PARCELAS:
            problemas.append((n + 1, carne, lado, "REGRA_CARNE", f"{len(lin_o)} parcelas no verso (esperado 1 a {MAX_PARCELAS})"))

        if (carne - 1) % a.amostra == 0:
            cod_o, cod_c = ler_codigos(po, a.dpi), ler_codigos(pc, a.dpi)
            cont["páginas com códigos lidos"] += 1
            cont["códigos de barras lidos"] += sum(v for (t, _), v in cod_o.items() if t == "ITF")
            cont["QR Codes lidos"] += sum(v for (t, _), v in cod_o.items() if t == "QR")
            if cod_o != cod_c:
                faltam = sum((cod_o - cod_c).values())
                problemas.append((n + 1, carne, lado, "CODIGO_LIDO",
                                  f"{faltam} código(s) do original não lidos ou diferentes no comprimido"))
            esperados = Counter(c for c in map(codigo_da_linha, lin_o) if c)
            lidos_itf = Counter(txt for (t, txt), v in cod_o.items() if t == "ITF" for _ in range(v))
            if esperados and esperados != lidos_itf:
                problemas.append((n + 1, carne, lado, "BARRAS_X_LINHA",
                                  f"códigos de barras lidos ({sum(lidos_itf.values())}) não batem com "
                                  f"as linhas digitáveis ({sum(esperados.values())})"))

        if (n + 1) % 100 == 0:
            print(f"  ... {n + 1}/{len(orig)} páginas  ({time.perf_counter() - t0:,.0f} s)")

    tempo = time.perf_counter() - t0
    print("\nResumo:")
    for chave in ("páginas comparadas", "linhas digitáveis", "páginas com códigos lidos",
                  "códigos de barras lidos", "QR Codes lidos"):
        print(f"  {chave:<28}: {cont[chave]:,}")
    print(f"  {'tempo':<28}: {tempo:,.1f} s")

    if problemas:
        print(f"\nREPROVADO: {len(problemas)} problema(s). Primeiros 20:")
        for pag, carne, lado, tipo, desc in problemas[:20]:
            onde = f"pág {pag} (carnê {carne}, {lado})" if pag else "arquivo"
            print(f"  - {onde}: [{tipo}] {desc}")
    else:
        print("\nAPROVADO: comprimido equivalente ao original em todas as verificações.")

    registrar_no_banco(a, len(orig), cont, problemas, tempo)
    return 1 if problemas else 0


def registrar_no_banco(a, paginas, cont, problemas, tempo) -> None:
    """Grava a validação (e cada problema) ligada ao registro do arquivo comprimido.

    O vínculo é pelo SHA-256 do comprimido: identifica exatamente aquele arquivo,
    byte a byte, mesmo que o mesmo nome tenha sido comprimido várias vezes.
    """
    hash_comprimido = sha256_arquivo(a.comprimido)
    engine = db.conectar()
    with Session(engine) as s:
        arquivo = s.scalars(select(db.Arquivo)
                            .where(db.Arquivo.sha256_comprimido == hash_comprimido)
                            .order_by(db.Arquivo.id.desc())).first()
        if arquivo is None:
            print("\nAVISO: este comprimido não foi gerado pelo comprimir_um.py com registro no banco "
                  "(nenhum arquivo com o mesmo SHA-256). Validação NÃO gravada.")
            return
        validacao = db.Validacao(arquivo=arquivo, amostra=a.amostra, dpi=a.dpi, paginas=paginas,
                                 linhas_digitaveis=cont["linhas digitáveis"],
                                 codigos_barras_lidos=cont["códigos de barras lidos"],
                                 qr_lidos=cont["QR Codes lidos"], aprovado=not problemas,
                                 tempo_s=round(tempo, 1))
        for pag, carne, lado, tipo, desc in problemas:
            validacao.problemas.append(db.Problema(pagina=pag, carne=carne, lado=lado,
                                                   tipo=tipo, descricao=desc))
        s.add(validacao)
        s.commit()
        print(f"\nRegistrado no banco: validação {validacao.id} do arquivo {arquivo.id} "
              f"({arquivo.nome}), {len(problemas)} problema(s)")


if __name__ == "__main__":
    # Códigos de saída: 0 = APROVADO, 1 = REPROVADO, 3 = a validação falhou ao rodar.
    # Sem isso, uma falha inesperada também sairia com 1 e seria confundida com reprovação.
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(3)
