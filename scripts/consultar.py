"""Fase 5.4: roda as consultas de auditoria (sql/consultas_auditoria.sql) e mostra o resultado.

Uso:
  python scripts\\consultar.py              lista as consultas disponíveis
  python scripts\\consultar.py resumo       roda uma consulta
  python scripts\\consultar.py todas        roda todas

Usa o mesmo banco do db.py (SQLite local, ou Oracle se ORACLE_DSN estiver definido).
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from sqlalchemy import text
import db

ARQUIVO_SQL = Path(__file__).resolve().parent.parent / "sql" / "consultas_auditoria.sql"


def carregar_consultas() -> dict[str, tuple[str, str]]:
    """Lê o .sql e separa as consultas pelo marcador '-- nome:'. Devolve {nome: (descrição, sql)}."""
    consultas = {}
    blocos = re.split(r"^-- nome:\s*", ARQUIVO_SQL.read_text(encoding="utf-8"), flags=re.M)[1:]
    for bloco in blocos:
        nome, resto = bloco.split("\n", 1)
        linhas = resto.strip().splitlines()
        descricao = linhas[0].removeprefix("--").strip() if linhas[0].startswith("--") else ""
        sql = "\n".join(l for l in linhas if not l.strip().startswith("--")).strip().rstrip(";")
        consultas[nome.strip()] = (descricao, sql)
    return consultas


def imprimir_tabela(colunas: list[str], linhas: list[tuple]) -> None:
    if not linhas:
        print("  (nenhuma linha)")
        return
    valores = [["" if v is None else str(v) for v in linha] for linha in linhas]
    larguras = [max(len(c), *(len(l[i]) for l in valores)) for i, c in enumerate(colunas)]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(colunas, larguras)))
    print("  " + "  ".join("-" * w for w in larguras))
    for linha in valores:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(linha, larguras)))


def main() -> int:
    consultas = carregar_consultas()
    if len(sys.argv) < 2:
        print("Consultas disponíveis:")
        for nome, (descricao, _) in consultas.items():
            print(f"  {nome:<14} {descricao}")
        print("\nUso: python scripts\\consultar.py <nome>   (ou 'todas')")
        return 0

    escolhidas = list(consultas) if sys.argv[1] == "todas" else sys.argv[1:]
    desconhecidas = [n for n in escolhidas if n not in consultas]
    if desconhecidas:
        print(f"Consulta(s) desconhecida(s): {', '.join(desconhecidas)}. Rode sem argumentos para ver a lista.")
        return 1

    engine = db.conectar(criar_tabelas=False)
    with engine.connect() as conexao:
        for nome in escolhidas:
            descricao, sql = consultas[nome]
            resultado = conexao.execute(text(sql))
            print(f"\n== {nome}: {descricao}")
            imprimir_tabela(list(resultado.keys()), resultado.fetchall())
    return 0


if __name__ == "__main__":
    sys.exit(main())
