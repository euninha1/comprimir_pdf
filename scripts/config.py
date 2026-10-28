"""Configuração central da ferramenta: lê o config.toml e as variáveis de ambiente.

Prioridade (quem vale mais primeiro):
  1. argumento na linha de comando   (ex.: --paralelo 5)       -> tratado em cada script
  2. variável de ambiente            (ex.: IPTU_LOTE_PARALELO=5)
  3. config.toml                     (na raiz do projeto, ou no caminho de IPTU_CONFIG)
  4. valor padrão definido abaixo

Variáveis de ambiente: IPTU_<SEÇÃO>_<CHAVE> em maiúsculas. Ex.: IPTU_BANCO_TIPO=oracle.
SENHAS NUNCA FICAM NO ARQUIVO: a senha do Oracle vem de IPTU_BANCO_ORACLE_SENHA
(ou é pedida no terminal).

Para ver a configuração em uso:  python scripts\\config.py
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

RAIZ_PROJETO = Path(__file__).resolve().parent.parent

PADROES: dict[str, dict] = {
    "banco": {
        "tipo": "sqlite",                    # sqlite | oracle | url
        "sqlite_arquivo": "data/auditoria.db",
        "oracle_dsn": "",                    # servidor:porta/servico
        "oracle_usuario": "",
        "url": "",                           # tipo = url: qualquer URL do SQLAlchemy (ex.: PostgreSQL)
    },
    "pastas": {
        "entrada": "",                       # ex.: S:\Modelo_IPTU (pode vir na linha de comando)
        "dados": "data",                     # onde ficam trabalho\, saida\ e quarentena\
        "logs": "logs",
        "subpasta_pdf24": "Comprimido",      # ignorada na entrada; usada para comparar tamanhos
    },
    "compressao": {
        "receita": 2,                        # 1 = sem perda; 2 = + imagens reduzidas (JPEG)
        "dpi": 150,
        "qualidade_jpeg": 80,
    },
    "validacao": {
        "amostra": 1,                        # ler códigos em 1 a cada N carnês (1 = todos)
        "dpi_leitura": 200,                  # resolução da "foto" para ler os códigos
        "max_parcelas": 12,
    },
    "lote": {
        "paralelo": 3,                       # arquivos ao mesmo tempo
        "aviso_a_cada_s": 60,                # intervalo do aviso "em andamento"
    },
}


def _converter(texto: str, exemplo):
    """Variáveis de ambiente chegam como texto: converte para o tipo do valor padrão."""
    if isinstance(exemplo, bool):
        return texto.strip().lower() in ("1", "true", "sim", "s", "yes")
    if isinstance(exemplo, int):
        return int(texto)
    if isinstance(exemplo, float):
        return float(texto)
    return texto


def arquivo_config() -> Path:
    return Path(os.environ.get("IPTU_CONFIG", RAIZ_PROJETO / "config.toml"))


def carregar() -> dict[str, dict]:
    """Junta padrões + config.toml + variáveis de ambiente (nessa ordem de prioridade)."""
    cfg = {secao: dict(valores) for secao, valores in PADROES.items()}
    caminho = arquivo_config()
    if caminho.exists():
        try:
            with open(caminho, "rb") as f:
                lido = tomllib.load(f)
        except tomllib.TOMLDecodeError as erro:
            sys.exit(f"ERRO de formato em {caminho}: {erro}\n"
                     "Dicas: caminhos do Windows entre aspas simples ('S:\\pasta'); "
                     "cada seção [nome] só pode aparecer uma vez; textos sempre entre aspas.")
        for secao, valores in lido.items():
            if secao not in cfg:
                sys.exit(f"ERRO em {caminho}: seção desconhecida [{secao}]. "
                         f"Válidas: {', '.join(cfg)}")
            for chave, valor in valores.items():
                if chave not in cfg[secao]:
                    sys.exit(f"ERRO em {caminho}: chave desconhecida '{chave}' em [{secao}]. "
                             f"Válidas: {', '.join(cfg[secao])}")
                cfg[secao][chave] = valor
    for secao, valores in cfg.items():
        for chave in valores:
            variavel = f"IPTU_{secao}_{chave}".upper()
            if variavel in os.environ:
                cfg[secao][chave] = _converter(os.environ[variavel], PADROES[secao][chave])
    return cfg


def sufixo_saida(cfg: dict) -> str:
    """Sufixo dos comprimidos, CALCULADO a partir da configuração (ex.: -r2-150dpi-q80.pdf)."""
    c = cfg["compressao"]
    if int(c["receita"]) == 1:
        return "-r1.pdf"
    return f"-r2-{c['dpi']}dpi-q{c['qualidade_jpeg']}.pdf"


def caminho(texto: str) -> Path:
    """Caminhos relativos do config valem a partir da RAIZ do projeto (não da pasta atual)."""
    p = Path(texto)
    return p if p.is_absolute() else RAIZ_PROJETO / p


if __name__ == "__main__":
    cfg = carregar()
    origem = arquivo_config()
    print(f"Arquivo de configuração: {origem} ({'encontrado' if origem.exists() else 'NÃO encontrado: usando padrões'})\n")
    for secao, valores in cfg.items():
        print(f"[{secao}]")
        for chave, valor in valores.items():
            variavel = f"IPTU_{secao}_{chave}".upper()
            marca = "   <- variável de ambiente" if variavel in os.environ else ""
            print(f"  {chave:<16} = {valor!r}{marca}")
        print()
    senha = "definida" if os.environ.get("IPTU_BANCO_ORACLE_SENHA") else "não definida (será pedida no terminal)"
    print(f"Senha do Oracle (IPTU_BANCO_ORACLE_SENHA): {senha}")
    print(f"Sufixo dos comprimidos: {sufixo_saida(cfg)}")
