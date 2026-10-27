"""Fase 6: processa uma pasta inteira de carnês (compressão + validação), vários arquivos em paralelo.

Para cada PDF encontrado (ignorando a subpasta Comprimido\\ do PDF24), em ordem natural
(1, 2, 3, ..., 10, 11, ...):
  1. consulta o banco para decidir o que fazer (RETOMADA, ver decidir())
  2. se precisar, roda scripts/comprimir_um.py (comprime e registra no banco)
  3. se precisar, roda scripts/validar.py      (valida e registra no banco)

Retomada: rodar o mesmo comando de novo continua de onde parou.
  PRONTO (saída intacta)            -> pula
  comprimido, sem validação         -> só valida
  REPROVADO                         -> pula e avisa (precisa de análise humana)
  ERRO / PROCESSANDO / sem registro -> processa
  original mudou de tamanho         -> processa de novo
  --forcar                          -> processa tudo de novo

Cada etapa roda num PROCESSO SEPARADO: se um PDF travar ou esgotar a memória,
só aquele arquivo falha e o lote segue para o próximo.

Uso:
  python scripts\\processar_lote.py "S:\\Modelo_IPTU"                  processa tudo
  python scripts\\processar_lote.py "S:\\Modelo_IPTU\\CDD_X" --limite 2  só os 2 primeiros (teste)
  python scripts\\processar_lote.py "S:\\Modelo_IPTU" --listar          mostra o que seria feito
Opções: --paralelo N (quantos arquivos ao mesmo tempo; padrão 3)
        --amostra N  (validação: lê códigos em 1 a cada N carnês; padrão 1 = todos)
        --forcar     (ignora o banco e processa tudo de novo)

Paralelismo: os arquivos são processados dos MAIORES para os menores (LPT), para que um
arquivo de 1 GB não fique para o fim com os outros processos parados esperando.
Só um lote pode rodar por vez: enquanto roda, existe o arquivo data\\lote.lock.

Pastas (todas espelham as pastas de CDD):
  data\\trabalho\\<CDD>\\    comprimido e ainda não validado (área temporária)
  data\\saida\\<CDD>\\       SÓ arquivos APROVADOS: tudo aqui pode ir para a gráfica
  data\\quarentena\\<CDD>\\  REPROVADOS ou não validados, cada um com um .motivo.txt
Um log completo de cada lote fica em logs\\lote_<data_hora>.log
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

import db
from comprimir_um import sha256_arquivo

PASTA_SCRIPTS = Path(__file__).resolve().parent
SCRIPT_COMPRIMIR = PASTA_SCRIPTS / "comprimir_um.py"
SCRIPT_VALIDAR = PASTA_SCRIPTS / "validar.py"
SUBPASTA_PDF24 = "comprimido"          # comparada sem diferenciar maiúsculas
SUFIXO_SAIDA = "-r2-150dpi-q80.pdf"    # receita decidida na Fase 3

PASTA_DADOS = Path("data")
ZONAS = ("trabalho", "saida", "quarentena")   # onde um comprimido pode estar

# Ações possíveis para cada arquivo
PROCESSAR, SO_VALIDAR, PULAR_PRONTO, PULAR_REPROVADO = "PROCESSAR", "SO_VALIDAR", "PULAR_PRONTO", "PULAR_REPROVADO"


def chave_natural(caminho: Path) -> list:
    """Ordem natural: '..._10.pdf' -> ['..._', 10, '.pdf'], para que 2 venha antes de 10."""
    return [int(parte) if parte.isdigit() else parte.lower()
            for parte in re.split(r"(\d+)", str(caminho))]


def listar_pdfs(raiz: Path) -> list[Path]:
    """Todos os PDFs abaixo de `raiz`, exceto os da subpasta do PDF24, em ordem natural."""
    encontrados = []
    for pasta, subpastas, arquivos in os.walk(raiz):
        subpastas[:] = [d for d in subpastas if d.lower() != SUBPASTA_PDF24]  # não desce no Comprimido
        encontrados += [Path(pasta) / a for a in arquivos if a.lower().endswith(".pdf")]
    return sorted(encontrados, key=chave_natural)


def referencia_pdf24(original: Path) -> Path | None:
    """Versão do PDF24, se existir: <pasta>\\Comprimido\\<nome>-compressed.pdf"""
    for sub in original.parent.iterdir():
        if sub.is_dir() and sub.name.lower() == SUBPASTA_PDF24:
            candidato = sub / f"{original.stem}-compressed.pdf"
            if candidato.exists():
                return candidato
    return None


def caminho_na_zona(original: Path, zona: str) -> Path:
    return PASTA_DADOS / zona / original.parent.name / f"{original.stem}{SUFIXO_SAIDA}"


def localizar_comprimido(original: Path, sha256: str | None) -> Path | None:
    """Procura o comprimido nas 3 zonas; só vale se o conteúdo (hash) for o registrado no banco."""
    for zona in ZONAS:
        caminho = caminho_na_zona(original, zona)
        if sha256 and caminho.exists() and sha256_arquivo(caminho) == sha256:
            return caminho
    return None


def mover_para(comprimido: Path, original: Path, zona: str, motivo: str | None = None) -> Path:
    """Move o comprimido para a zona (saida/quarentena) e cuida do arquivo .motivo.txt."""
    destino = caminho_na_zona(original, zona)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if comprimido.resolve() != destino.resolve():
        os.replace(comprimido, destino)            
    for z in ZONAS:                                
        caminho_na_zona(original, z).with_suffix(".motivo.txt").unlink(missing_ok=True)
    if motivo:
        destino.with_suffix(".motivo.txt").write_text(motivo, encoding="utf-8")
    return destino


def motivo_da_quarentena(sessao: Session, original: Path) -> str:
    """Texto do .motivo.txt: os problemas da última validação, ou o aviso de que ela não rodou."""
    registro = ultimo_registro(sessao, original)
    validacao = ultima_validacao(sessao, registro.id) if registro else None
    cabecalho = f"Arquivo original: {original}\nQuarentena em: {datetime.now():%d/%m/%Y %H:%M}\n\n"
    if validacao is None:
        return cabecalho + "A validação NÃO conseguiu rodar. Ver o log do lote (pasta logs).\n"
    linhas = [f"- pág {p.pagina} ({p.lado}) [{p.tipo}] {p.descricao}" if p.pagina
              else f"- [{p.tipo}] {p.descricao}" for p in validacao.problemas]
    return cabecalho + f"REPROVADO na validação ({len(linhas)} problema(s)):\n" + "\n".join(linhas) + "\n"


def ultimo_registro(sessao: Session, original: Path):
    return sessao.scalars(select(db.Arquivo)
                          .where(db.Arquivo.cdd == original.parent.name,
                                 db.Arquivo.nome == original.name)
                          .order_by(db.Arquivo.id.desc())).first()


def ultima_validacao(sessao: Session, arquivo_id: int):
    return sessao.scalars(select(db.Validacao)
                          .where(db.Validacao.arquivo_id == arquivo_id)
                          .order_by(db.Validacao.id.desc())).first()


def decidir(sessao: Session, original: Path, forcar: bool) -> tuple[str, str, Path | None]:
    """RETOMADA: olha o último registro deste arquivo no banco e decide (ação, motivo, comprimido)."""
    if forcar:
        return PROCESSAR, "--forcar", None
    registro = ultimo_registro(sessao, original)
    if registro is None:
        return PROCESSAR, "novo", None
    if registro.bytes_original != original.stat().st_size:
        return PROCESSAR, "o original mudou desde a última compressão", None
    if registro.status == "PROCESSANDO":
        return PROCESSAR, "tinha sido interrompido", None
    if registro.status == "ERRO":
        return PROCESSAR, "nova tentativa após ERRO", None
    comprimido = localizar_comprimido(original, registro.sha256_comprimido)
    if comprimido is None:
        return PROCESSAR, "comprimido ausente ou alterado", None
    validacao = ultima_validacao(sessao, registro.id)
    if validacao is None:
        return SO_VALIDAR, "comprimido, falta validar", comprimido
    if validacao.aprovado:
        return PULAR_PRONTO, "já estava PRONTO", comprimido
    return PULAR_REPROVADO, "já estava REPROVADO (ver: consultar.py problemas)", comprimido


def rodar(argumentos: list[str], saida: list[str]) -> int:
    """Roda um script Python num processo separado; guarda a saída em `saida` (vai para o log)."""
    ambiente = {**os.environ, "PYTHONUTF8": "1"}  
    proc = subprocess.run([sys.executable, *argumentos], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=ambiente)
    saida.append(f"\n$ python {' '.join(argumentos)}\n{proc.stdout}{proc.stderr}")
    return proc.returncode


def processar_um(engine, original: Path, acao: str, comprimido: Path | None,
                 amostra: int) -> tuple[str, str]:
    """Comprime (se precisar), valida e publica UM arquivo. Roda em paralelo com outros.

    Devolve (situação, texto para o log). Cada arquivo usa só os SEUS caminhos e a SUA
    sessão do banco, então vários podem rodar ao mesmo tempo sem interferir.
    """
    saida: list[str] = []
    if acao == PROCESSAR:
        comprimido = caminho_na_zona(original, "trabalho")
        args = [str(SCRIPT_COMPRIMIR), str(original), "--receita", "2",
                "--saida", str(comprimido.parent)]
        ref = referencia_pdf24(original)
        if ref:
            args += ["--referencia", str(ref)]
        if rodar(args, saida) != 0:
            return "ERRO", "".join(saida)
    codigo = rodar([str(SCRIPT_VALIDAR), str(original), str(comprimido),
                    "--amostra", str(amostra)], saida)
    situacao = {0: "PRONTO", 1: "REPROVADO"}.get(codigo, "ERRO")
    if situacao == "PRONTO":
        mover_para(comprimido, original, "saida")
    else:
        with Session(engine) as s:
            mover_para(comprimido, original, "quarentena", motivo_da_quarentena(s, original))
    return situacao, "".join(saida)


def travar() -> Path | None:
    """Cria data\\lote.lock; se já existir, outro lote está rodando e devolve None."""
    trava = PASTA_DADOS / "lote.lock"
    PASTA_DADOS.mkdir(exist_ok=True)
    try:
        fd = os.open(trava, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  
    except FileExistsError:
        print(f"\nERRO: já existe um lote rodando ({trava}):\n  "
              f"{trava.read_text(encoding='utf-8', errors='replace')}\n"
              "Se tiver certeza de que nenhum lote está rodando (ex.: o PC desligou no meio), "
              "apague esse arquivo e rode de novo.")
        return None
    os.write(fd, f"{socket.gethostname()}, processo {os.getpid()}, desde {datetime.now():%d/%m/%Y %H:%M}"
             .encode("utf-8"))
    os.close(fd)
    return trava


def main() -> int:
    ap = argparse.ArgumentParser(description="Fase 6: processa uma pasta inteira")
    ap.add_argument("pasta", type=Path, help="pasta raiz (ex.: S:\\Modelo_IPTU) ou de um CDD")
    ap.add_argument("--paralelo", type=int, default=3, help="quantos arquivos ao mesmo tempo (padrão 3)")
    ap.add_argument("--amostra", type=int, default=1, help="validação: 1 a cada N carnês")
    ap.add_argument("--limite", type=int, help="processar só os N primeiros arquivos (teste)")
    ap.add_argument("--listar", action="store_true", help="só mostra o que seria feito, não processa")
    ap.add_argument("--forcar", action="store_true", help="ignora o banco e processa tudo de novo")
    a = ap.parse_args()

    faltando = [s.name for s in (SCRIPT_COMPRIMIR, SCRIPT_VALIDAR) if not s.exists()]
    if faltando:
        print(f"ERRO: não encontrei {', '.join(faltando)} em {PASTA_SCRIPTS}. "
              "Os scripts precisam ter exatamente esses nomes.")
        return 2
    if os.environ.get("ORACLE_DSN") and not os.environ.get("ORACLE_PASSWORD"):
        os.environ["ORACLE_PASSWORD"] = getpass.getpass(f"Senha Oracle de {os.environ.get('ORACLE_USER')}: ")

    pdfs = listar_pdfs(a.pasta)[: a.limite]
    print(f"\nPasta   : {a.pasta}")
    print(f"Arquivos: {len(pdfs)} PDF(s), {sum(p.stat().st_size for p in pdfs) / 1024**3:,.2f} GB "
          "(subpasta Comprimido ignorada)")

    engine = db.conectar()
    planos = []
    with Session(engine) as s:
        for original in pdfs:
            planos.append((original, *decidir(s, original, a.forcar)))
    a_fazer = sum(1 for p in planos if p[1] in (PROCESSAR, SO_VALIDAR))
    print(f"A fazer : {a_fazer} arquivo(s); {len(planos) - a_fazer} já resolvido(s) (serão pulados)")

    if a.listar:
        for original, acao, motivo, _ in planos:
            print(f"  {acao:<15} {original.relative_to(a.pasta)}  ({motivo})")
        return 0

    trava = travar()
    if trava is None:
        return 2
    with Session(engine) as s:
        lote = db.Lote(status="EM_ANDAMENTO", pasta=str(a.pasta), paralelo=a.paralelo,
                       amostra=a.amostra, arquivos_a_fazer=a_fazer)
        s.add(lote)
        s.commit()
        lote_id = lote.id
    os.environ["LOTE_ID"] = str(lote_id)
    contagem = {"PRONTO": 0, "REPROVADO": 0, "ERRO": 0, "JA_PRONTO": 0, "JA_REPROVADO": 0}
    inicio = time.perf_counter()
    codigo = 130                      
    try:
        codigo = executar(engine, planos, a_fazer, a, contagem, lote_id)
        return codigo
    finally:
        with Session(engine) as s:
            lote = s.get(db.Lote, lote_id)
            lote.finalizado_em = db.agora()
            lote.duracao_s = round(time.perf_counter() - inicio, 1)
            lote.status = "INTERROMPIDO" if codigo == 130 else "CONCLUIDO"
            lote.prontos, lote.reprovados, lote.erros = (contagem["PRONTO"], contagem["REPROVADO"],
                                                         contagem["ERRO"])
            lote.pulados = contagem["JA_PRONTO"] + contagem["JA_REPROVADO"]
            s.commit()
        trava.unlink(missing_ok=True)


def executar(engine, planos, a_fazer: int, a, contagem: dict, lote_id: int) -> int:
    Path("logs").mkdir(exist_ok=True)
    caminho_log = Path("logs") / f"lote_{datetime.now():%Y%m%d_%H%M%S}.log"
    print(f"Lote    : {lote_id} (registrado no banco)")
    print(f"Log     : {caminho_log}")
    print(f"Paralelo: {a.paralelo} arquivo(s) ao mesmo tempo, dos maiores para os menores\n")
    inicio, feitos = time.perf_counter(), 0
    trava_log = threading.Lock()   
    for original, acao, motivo, comprimido in planos:
        if acao == PULAR_PRONTO:
            mover_para(comprimido, original, "saida")
            contagem["JA_PRONTO"] += 1
        elif acao == PULAR_REPROVADO:
            with Session(engine) as s:
                mover_para(comprimido, original, "quarentena", motivo_da_quarentena(s, original))
            contagem["JA_REPROVADO"] += 1
    if contagem["JA_PRONTO"] or contagem["JA_REPROVADO"]:
        print(f"Pulados : {contagem['JA_PRONTO']} já PRONTO(s), "
              f"{contagem['JA_REPROVADO']} já REPROVADO(s)\n")

   
    pendentes = sorted((p for p in planos if p[1] in (PROCESSAR, SO_VALIDAR)),
                       key=lambda p: p[0].stat().st_size, reverse=True)
    with open(caminho_log, "w", encoding="utf-8") as log, \
            ThreadPoolExecutor(max_workers=a.paralelo) as executor:
        tarefas = {executor.submit(processar_um, engine, original, acao, comprimido, a.amostra):
                   (original, motivo, time.perf_counter())
                   for original, acao, motivo, comprimido in pendentes}
        try:
            for tarefa in as_completed(tarefas):
                original, motivo, t0 = tarefas[tarefa]
                try:
                    situacao, texto = tarefa.result()
                except Exception as erro:        
                    situacao, texto = "ERRO", f"\nfalha no lote: {type(erro).__name__}: {erro}\n"
                with trava_log:
                    log.write(f"\n===== {original.parent.name}\\{original.name}: {situacao}{texto}\n")
                    log.flush()
                contagem[situacao] += 1
                feitos += 1
                restante = (time.perf_counter() - inicio) / feitos * (a_fazer - feitos)
                print(f"[{feitos}/{a_fazer}] {original.parent.name}\\{original.name} ... {situacao}  "
                      f"({motivo}; {time.perf_counter() - t0:,.0f} s; faltam ~{restante / 60:,.0f} min)",
                      flush=True)
        except KeyboardInterrupt:
            executor.shutdown(wait=False, cancel_futures=True)
            print("\nInterrompido. O que terminou está registrado; rode o MESMO comando para continuar.")
            return 130

    segundos = time.perf_counter() - inicio
    print(f"\nResumo do lote {lote_id} ({segundos:,.0f} s = {segundos / 60:,.1f} min):")
    print(f"  PRONTO     : {contagem['PRONTO']}   (+{contagem['JA_PRONTO']} que já estavam prontos)")
    print(f"  REPROVADO  : {contagem['REPROVADO']}   (+{contagem['JA_REPROVADO']} que já estavam reprovados)")
    print(f"  ERRO       : {contagem['ERRO']}")
    print("\n  Aprovados (podem ir para a gráfica): data\\saida\\")
    print("  Reprovados / não validados:         data\\quarentena\\  (ver os .motivo.txt)")
    print(f"\nDetalhes de cada arquivo: {caminho_log}")
    print("Situação no banco:         python scripts\\consultar.py resumo")
    return 0 if contagem["ERRO"] == contagem["REPROVADO"] == contagem["JA_REPROVADO"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
