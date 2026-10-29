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
Opções: --dados PASTA (onde ficam trabalho\\, saida\\ e quarentena\\; padrão: data\\ do projeto)
                     ex.: --dados "S:\\Modelo_IPTU_Comprimido". O banco e os logs ficam SEMPRE
                     locais (o SQLite pode corromper em pasta de rede).
        --paralelo N (quantos arquivos ao mesmo tempo; padrão 3)
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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
import db
from comprimir_um import sha256_arquivo

PASTA_SCRIPTS = Path(__file__).resolve().parent
SCRIPT_COMPRIMIR = PASTA_SCRIPTS / "comprimir_um.py"
SCRIPT_VALIDAR = PASTA_SCRIPTS / "validar.py"
# Valores definidos em main() a partir do config.toml (ver scripts/config.py)
SUBPASTA_PDF24 = "comprimido"          # comparada sem diferenciar maiúsculas
SUFIXO_SAIDA = "-r2-150dpi-q80.pdf"    # CALCULADO da configuração (receita, dpi, qualidade)
CFG: dict = {}

PASTA_DADOS = Path("data")   # trocada por --dados; as 3 zonas ficam SEMPRE juntas (mesmo disco)
ZONAS = ("trabalho", "saida", "quarentena")   # onde um comprimido pode estar

# Ações possíveis para cada arquivo
PROCESSAR, SO_VALIDAR, PULAR_PRONTO, PULAR_REPROVADO = "PROCESSAR", "SO_VALIDAR", "PULAR_PRONTO", "PULAR_REPROVADO"


def chave_natural(caminho: Path) -> list:
    """Ordem natural: '..._10.pdf' -> ['..._', 10, '.pdf'], para que 2 venha antes de 10."""
    return [int(parte) if parte.isdigit() else parte.lower()
            for parte in re.split(r"(\d+)", str(caminho))]


def listar_pdfs(raiz: Path, ignorar: Path | None = None) -> list[Path]:
    """Todos os PDFs ORIGINAIS abaixo de `raiz`, em ordem natural. Ficam de fora:
    - a subpasta Comprimido\\ (versões do PDF24);
    - a pasta `ignorar` (a pasta de saídas do --dados, caso esteja DENTRO da entrada);
    - qualquer arquivo já comprimido por nós (nome terminando em -r2-150dpi-q80.pdf).
    Sem isso, os próprios comprimidos seriam tratados como originais na execução seguinte.
    """
    ignorar = ignorar.resolve() if ignorar else None
    encontrados = []
    for pasta, subpastas, arquivos in os.walk(raiz):
        subpastas[:] = [d for d in subpastas
                        if d.lower() != SUBPASTA_PDF24                       # não desce no Comprimido
                        and (ignorar is None or (Path(pasta) / d).resolve() != ignorar)]  # nem na saída
        encontrados += [Path(pasta) / a for a in arquivos
                        if a.lower().endswith(".pdf") and not a.lower().endswith(SUFIXO_SAIDA)]
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
        os.replace(comprimido, destino)            # mesmo disco: instantâneo, conteúdo intacto
    # Uma única versão por original: se a receita mudou (ex.: DPI 150 -> 200), remove as
    # versões antigas DESTE original em saida\\ e quarentena\\ (ex.: ..._1-r2-150dpi-q80.pdf).
    # O padrão "<nome>-r*" não pega outros arquivos: "..._1-r*" não casa com "..._10-r...".
    for z in ("saida", "quarentena"):
        pasta = PASTA_DADOS / z / original.parent.name
        if pasta.is_dir():
            for antigo in pasta.glob(f"{original.stem}-r*.pdf"):
                if antigo.resolve() != destino.resolve():
                    antigo.unlink(missing_ok=True)
                    antigo.with_suffix(".motivo.txt").unlink(missing_ok=True)
    for z in ZONAS:                                # remove .motivo.txt antigos de outras zonas
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
    # status OK: o comprimido precisa existir (em alguma zona) e ser exatamente o registrado
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
    ambiente = {**os.environ, "PYTHONUTF8": "1"}  # acentos corretos no Windows
    proc = subprocess.run([sys.executable, *argumentos], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=ambiente)
    saida.append(f"\n$ python {' '.join(argumentos)}\n{proc.stdout}{proc.stderr}")
    return proc.returncode


_TELA = threading.Lock()          # vários arquivos escrevem na tela: um de cada vez
_EM_ANDAMENTO = [0]               # quantos arquivos estão sendo processados agora
_INICIOS: dict[Path, float] = {}  # arquivo -> quando começou (para a estimativa do "faltam")
DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def avisar(mensagem: str, destaque: bool = False) -> None:
    """Mostra uma linha na tela com o horário (HH:MM); `destaque` = separada por linha em branco."""
    with _TELA:
        print(f"{chr(10) if destaque else ''}  {datetime.now():%H:%M}  {mensagem}", flush=True)


def cdd_curto(original: Path) -> str:
    """'CDD_Aracaju_Zona_Sul_49030000_a_49049999' -> 'Aracaju_Zona_Sul' (coluna da tela)."""
    nome = re.sub(r"^CDD_", "", original.parent.name, flags=re.IGNORECASE)
    return re.sub(r"_\d+_a_\d+$", "", nome)[:22]


def linha_arquivo(status: str, original: Path, resto: str) -> str:
    return f"{status:<10} {cdd_curto(original):<22} {original.name}   {resto}"


def nome_curto(original: Path) -> str:
    return f"{original.parent.name}\\{original.name}"


def mb(n_bytes: float) -> str:
    """1174405120 -> '1.120 MB'; 21390950 -> '20,4 MB' (padrão brasileiro)."""
    valor = n_bytes / 1024**2
    texto = f"{valor:,.0f}" if valor >= 100 else f"{valor:,.1f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".") + " MB"


def resumo_problema(engine, original: Path, texto: str, situacao: str) -> str:
    """Uma linha explicando um REPROVADO (1º problema da validação) ou um ERRO (última linha da saída)."""
    if situacao == "REPROVADO":
        with Session(engine) as s:
            registro = ultimo_registro(s, original)
            validacao = ultima_validacao(s, registro.id) if registro else None
            if validacao and validacao.problemas:
                p = validacao.problemas[0]
                onde = f" pág {p.pagina}" if p.pagina else ""
                mais = len(validacao.problemas) - 1
                return f"{p.tipo}{onde}" + (f" (+{mais} problema(s))" if mais else "")
    # ERRO: a mensagem do próprio script ("ERRO: ...") ou a última linha do traceback
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    candidatas = ([l for l in linhas if l.startswith("ERRO")]
                  or [l for l in linhas if re.match(r"^\w+(Error|Exception)\b", l)] or linhas[-1:])
    return (candidatas[-1][:90] if candidatas else "sem detalhes")


def processar_um(engine, original: Path, acao: str, comprimido: Path | None,
                 amostra: int) -> dict:
    """Comprime (se precisar), valida e publica UM arquivo. Roda em paralelo com outros.

    Devolve {situacao, texto (para o log), compressao_s, validacao_s, total_s}. Os tempos são
    medidos AQUI DENTRO, a partir do momento em que o arquivo começa de fato (o tempo em que
    ele esperou na fila não entra). Cada arquivo usa só os SEUS caminhos e a SUA sessão do
    banco, então vários podem rodar ao mesmo tempo sem interferir.
    """
    with _TELA:
        _EM_ANDAMENTO[0] += 1
        _INICIOS[original] = time.perf_counter()
    avisar(linha_arquivo("inicia", original, mb(original.stat().st_size)
                         + ("" if acao == PROCESSAR else "   (só validar)")))
    inicio = time.perf_counter()
    r = {"compressao_s": 0.0, "validacao_s": 0.0}
    saida: list[str] = []
    try:
        # 1) Compressão (se precisar), sempre na zona de trabalho
        if acao == PROCESSAR:
            comprimido = caminho_na_zona(original, "trabalho")
            c = CFG["compressao"]
            args = [str(SCRIPT_COMPRIMIR), str(original), "--receita", str(c["receita"]),
                    "--dpi", str(c["dpi"]), "--qualidade", str(c["qualidade_jpeg"]),
                    "--saida", str(comprimido.parent)]
            ref = referencia_pdf24(original)
            if ref:
                args += ["--referencia", str(ref)]
            t = time.perf_counter()
            codigo = rodar(args, saida)
            r["compressao_s"] = time.perf_counter() - t
            if codigo != 0:
                r["situacao"] = "ERRO"
                return r
        # 2) Validação. Código de saída do validar.py: 0 = aprovado, 1 = reprovado,
        #    outro = a validação NÃO conseguiu rodar (isso é ERRO, não reprovação).
        t = time.perf_counter()
        codigo = rodar([str(SCRIPT_VALIDAR), str(original), str(comprimido),
                        "--amostra", str(amostra)], saida)
        r["validacao_s"] = time.perf_counter() - t
        r["situacao"] = {0: "PRONTO", 1: "REPROVADO"}.get(codigo, "ERRO")
        # 3) Publica: aprovado vai para a saída; o resto vai para a quarentena
        if r["situacao"] == "PRONTO":
            r["bytes_final"] = mover_para(comprimido, original, "saida").stat().st_size
        else:
            with Session(engine) as s:
                mover_para(comprimido, original, "quarentena", motivo_da_quarentena(s, original))
        return r
    finally:
        r["texto"] = "".join(saida)
        r["total_s"] = time.perf_counter() - inicio
        with _TELA:
            _EM_ANDAMENTO[0] -= 1
            _INICIOS.pop(original, None)


def estimar_faltam(pendentes_mb: dict[Path, float], concluidos: list[tuple[float, float]],
                   paralelo: int) -> float | None:
    """Segundos que faltam, considerando TAMANHO e PARALELISMO.

    ritmo  = segundos por MB, medido nos arquivos já concluídos
    falta  = (MB dos que ainda nem começaram) * ritmo
           + (o que falta dos que estão em andamento: tamanho * ritmo - tempo já gasto)
    Se ainda há fila, os 'paralelo' processos dividem o trabalho; se não há mais fila,
    o lote termina quando o mais demorado dos que estão rodando terminar.
    """
    if not concluidos:
        return None
    ritmo = sum(s for s, _ in concluidos) / max(sum(mb for _, mb in concluidos), 0.001)
    agora = time.perf_counter()
    with _TELA:
        rodando = {arq: agora - ini for arq, ini in _INICIOS.items()}
    falta_rodando = [max(0.0, pendentes_mb[a] * ritmo - gasto) for a, gasto in rodando.items()
                     if a in pendentes_mb]
    falta_fila = sum(mb * ritmo for a, mb in pendentes_mb.items() if a not in rodando)
    if falta_fila == 0:
        return max(falta_rodando, default=0.0)
    return (falta_fila + sum(falta_rodando)) / paralelo


def texto_faltam(segundos: float | None) -> str:
    """'faltam ~46 h | término ~sáb 09:00' (vazio antes do 1º arquivo terminar)."""
    if segundos is None:
        return ""
    if segundos < 90:
        duracao = f"{segundos:,.0f} s"
    elif segundos < 90 * 60:
        duracao = f"{segundos / 60:,.0f} min"
    else:
        duracao = f"{segundos / 3600:,.1f} h".replace(".", ",")
    fim = datetime.now() + timedelta(seconds=segundos)
    dia = "hoje" if fim.date() == datetime.now().date() else DIAS[fim.weekday()]
    return f" | faltam ~{duracao} | término ~{dia} {fim:%H:%M}"


def pasta_gravavel(pasta: Path) -> bool:
    """Confere ANTES de começar se dá para criar e apagar arquivos na pasta de dados."""
    teste = pasta / f".teste_gravacao_{os.getpid()}.tmp"
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        teste.write_bytes(b"ok")
        teste.unlink()
        return True
    except OSError as erro:
        print(f"\nERRO: não consegui gravar em {pasta}: {erro}\n"
              "Confira se a pasta existe e se você tem permissão de escrita.")
        return False


def travar() -> Path | None:
    """Cria data\\lote.lock; se já existir, outro lote está rodando e devolve None."""
    trava = PASTA_DADOS / "lote.lock"
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(trava, os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # falha se já existir
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
    global PASTA_DADOS, SUBPASTA_PDF24, SUFIXO_SAIDA, CFG
    CFG = config.carregar()   # padrões vêm do config.toml; a linha de comando sobrescreve
    ap.add_argument("pasta", type=Path, nargs="?",
                    default=Path(CFG["pastas"]["entrada"]) if CFG["pastas"]["entrada"] else None,
                    help="pasta raiz ou de um CDD (padrão: pastas.entrada do config.toml)")
    ap.add_argument("--dados", type=Path, default=config.caminho(CFG["pastas"]["dados"]),
                    help="onde ficam trabalho, saida e quarentena (padrão: pastas.dados do config.toml)")
    ap.add_argument("--paralelo", type=int, default=CFG["lote"]["paralelo"],
                    help="quantos arquivos ao mesmo tempo (padrão: lote.paralelo do config.toml)")
    ap.add_argument("--amostra", type=int, default=CFG["validacao"]["amostra"],
                    help="validação: 1 a cada N carnês (padrão: validacao.amostra do config.toml)")
    ap.add_argument("--limite", type=int, help="processar só os N primeiros arquivos (teste)")
    ap.add_argument("--listar", action="store_true", help="só mostra o que seria feito, não processa")
    ap.add_argument("--forcar", action="store_true", help="ignora o banco e processa tudo de novo")
    a = ap.parse_args()
    if a.pasta is None:
        ap.error("informe a pasta de entrada ou preencha pastas.entrada no config.toml")
    PASTA_DADOS = a.dados
    SUBPASTA_PDF24 = CFG["pastas"]["subpasta_pdf24"].lower()
    SUFIXO_SAIDA = config.sufixo_saida(CFG)

    # Confere ANTES de começar se os scripts existem com o nome certo
    faltando = [s.name for s in (SCRIPT_COMPRIMIR, SCRIPT_VALIDAR) if not s.exists()]
    if faltando:
        print(f"ERRO: não encontrei {', '.join(faltando)} em {PASTA_SCRIPTS}. "
              "Os scripts precisam ter exatamente esses nomes.")
        return 2
    # Oracle: pede a senha UMA vez aqui; os processos filhos herdam pela variável de ambiente
    if CFG["banco"]["tipo"].lower() == "oracle" and not os.environ.get("IPTU_BANCO_ORACLE_SENHA"):
        os.environ["IPTU_BANCO_ORACLE_SENHA"] = getpass.getpass(
            f"Senha Oracle de {CFG['banco']['oracle_usuario']}: ")

    pdfs = listar_pdfs(a.pasta, ignorar=PASTA_DADOS)[: a.limite]
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

    if not pasta_gravavel(PASTA_DADOS):
        return 2
    trava = travar()
    if trava is None:
        return 2
    # Registra o LOTE no banco; os processos filhos recebem o id pela variável LOTE_ID
    with Session(engine) as s:
        lote = db.Lote(status="EM_ANDAMENTO", pasta=str(a.pasta), paralelo=a.paralelo,
                       amostra=a.amostra, arquivos_a_fazer=a_fazer)
        s.add(lote)
        s.commit()
        lote_id = lote.id
    os.environ["LOTE_ID"] = str(lote_id)
    contagem = {"PRONTO": 0, "REPROVADO": 0, "ERRO": 0, "JA_PRONTO": 0, "JA_REPROVADO": 0}
    inicio = time.perf_counter()
    codigo = 130                       # se algo inesperado acontecer, fica como INTERROMPIDO
    try:
        codigo = executar(engine, planos, a_fazer, a, contagem, lote_id)
        return codigo
    finally:
        # Tempo do LOTE: relógio de início ao fim (com paralelismo, menor que a soma dos arquivos)
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
    pasta_logs = config.caminho(CFG["pastas"]["logs"])
    pasta_logs.mkdir(parents=True, exist_ok=True)
    caminho_log = pasta_logs / f"lote_{datetime.now():%Y%m%d_%H%M%S}.log"
    cabecalho = (f"LOTE {lote_id}  |  {a_fazer} arquivo(s)  |  {a.paralelo} em paralelo  |  "
                 f"saídas: {PASTA_DADOS.resolve()}")
    print(f"\n{cabecalho}\nLog com os detalhes: {caminho_log}\n{'=' * min(len(cabecalho), 100)}")
    inicio, feitos = time.perf_counter(), 0
    trava_log = threading.Lock()   # vários arquivos terminam ao mesmo tempo: um escreve por vez

    # 1) Os já resolvidos: rápidos, um por vez (só confere a pasta certa)
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

    # 2) Os que precisam de trabalho: em paralelo, maiores primeiro (LPT)
    pendentes = sorted((p for p in planos if p[1] in (PROCESSAR, SO_VALIDAR)),
                       key=lambda p: p[0].stat().st_size, reverse=True)
    with open(caminho_log, "w", encoding="utf-8") as log, \
            ThreadPoolExecutor(max_workers=a.paralelo) as executor:
        tarefas = {executor.submit(processar_um, engine, original, acao, comprimido, a.amostra): original
                   for original, acao, motivo, comprimido in pendentes}
        abertas = set(tarefas)
        pendentes_mb = {original: original.stat().st_size / 1024**2 for original in tarefas.values()}
        concluidos: list[tuple[float, float]] = []   # (segundos, MB) de cada arquivo terminado
        try:
            while abertas:
                # espera até aviso_a_cada_s; se nada terminar, mostra QUAIS arquivos estão
                # em processamento e há quanto tempo (assim dá para ver se algum travou)
                prontas, abertas = wait(abertas, timeout=CFG["lote"]["aviso_a_cada_s"],
                                        return_when=FIRST_COMPLETED)
                if not prontas:
                    agora = time.perf_counter()
                    with _TELA:
                        rodando = sorted(_INICIOS.items(), key=lambda item: item[1])
                    lista = ", ".join(f"{arq.stem} ({(agora - ini) / 60:,.0f} min)" for arq, ini in rodando)
                    avisar(f"... processando: {lista}", destaque=True)
                    continue
                for tarefa in prontas:
                    original = tarefas[tarefa]
                    try:
                        r = tarefa.result()
                    except Exception as erro:          # falha no próprio lote (ex.: mover arquivo)
                        r = {"situacao": "ERRO", "compressao_s": 0, "validacao_s": 0, "total_s": 0,
                             "texto": f"\nfalha no lote: {type(erro).__name__}: {erro}\n"}
                    with trava_log:
                        log.write(f"\n===== {nome_curto(original)}: {r['situacao']} "
                                  f"(compressão {r['compressao_s']:.0f} s, validação {r['validacao_s']:.0f} s, "
                                  f"total {r['total_s']:.0f} s){r['texto']}\n")
                        log.flush()
                    contagem[r["situacao"]] += 1
                    feitos += 1
                    concluidos.append((r["total_s"], pendentes_mb.pop(original)))
                    posicao = f"[{feitos:>{len(str(a_fazer))}}/{a_fazer} {100 * feitos // a_fazer:>3}%]"
                    tempo = f"{r['total_s'] / 60:,.0f} min" if r["total_s"] >= 90 else f"{r['total_s']:,.0f} s"
                    if r["situacao"] == "PRONTO":
                        antes, depois = original.stat().st_size, r["bytes_final"]
                        detalhe = (f"{mb(antes)} -> {mb(depois)} "
                                   f"(-{100 * (1 - depois / antes):.0f}%)   {tempo}")
                        status = "OK"
                    else:
                        destino = "-> quarentena" if r["situacao"] == "REPROVADO" else "(ver log)"
                        detalhe = f"{resumo_problema(engine, original, r['texto'], r['situacao'])} {destino}"
                        status = r["situacao"]
                    avisar(linha_arquivo(status, original, f"{detalhe}   {posicao}"))
                    # a cada 10 arquivos (e no último): o placar e a previsão de término
                    if feitos % 10 == 0 or feitos == a_fazer:
                        restante = estimar_faltam(pendentes_mb, concluidos, a.paralelo)
                        avisar(f">> progresso: {feitos}/{a_fazer} ({100 * feitos // a_fazer}%) | "
                               f"{contagem['PRONTO']} OK, {contagem['REPROVADO']} reprovado(s), "
                               f"{contagem['ERRO']} erro(s){texto_faltam(restante) if feitos < a_fazer else ''}", destaque=True)
        except KeyboardInterrupt:
            executor.shutdown(wait=False, cancel_futures=True)
            print("\nInterrompido. O que terminou está registrado; rode o MESMO comando para continuar.")
            return 130

    segundos = time.perf_counter() - inicio
    print(f"\nResumo do lote {lote_id} ({segundos:,.0f} s = {segundos / 60:,.1f} min):")
    print(f"  PRONTO     : {contagem['PRONTO']}   (+{contagem['JA_PRONTO']} que já estavam prontos)")
    print(f"  REPROVADO  : {contagem['REPROVADO']}   (+{contagem['JA_REPROVADO']} que já estavam reprovados)")
    print(f"  ERRO       : {contagem['ERRO']}")
    print(f"\n  Aprovados (podem ir para a gráfica): {PASTA_DADOS.resolve() / 'saida'}")
    print(f"  Reprovados / não validados:         {PASTA_DADOS.resolve() / 'quarentena'}  (ver os .motivo.txt)")
    print(f"\nDetalhes de cada arquivo: {caminho_log}")
    print("Situação no banco:         python scripts\\consultar.py resumo")
    return 0 if contagem["ERRO"] == contagem["REPROVADO"] == contagem["JA_REPROVADO"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
