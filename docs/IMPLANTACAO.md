# Guia de implantação: compressão automática dos carnês de IPTU

Destinado à equipe de arquitetura. Visão geral do projeto: `docs/visao_geral.md`.

## 1. O que a ferramenta faz

Para cada PDF de carnês (≈ 500 carnês / 1.000 páginas A3 por arquivo):

1. **comprime** (remove imagens repetidas; reduz imagens decorativas para 150 DPI, JPEG q80);
2. **valida** contra o original (páginas, texto, linhas digitáveis, regra do carnê e
   leitura por software de todos os códigos de barras ITF e QR Codes);
3. **publica**: aprovado → `saida\`; reprovado ou não validado → `quarentena\` + `.motivo.txt`;
4. **registra** tudo no banco de auditoria (lote, execução, arquivo, validação, problemas).

O original nunca é alterado. Critérios de validação: `docs/criterios_validacao.md`.

## 2. Requisitos

| Item | Valor |
|---|---|
| Python | 3.11 ou superior (validado em 3.13) |
| Sistema | Windows (validado) ou Linux |
| Memória | ≈ 400 MB por arquivo em processamento (arquivo de 250 MB); medir arquivos de 1 GB+ |
| Disco das saídas | ≈ 10% do volume de entrada (307 GB → ≈ 30 GB) |
| Rede | Leitura dos originais e gravação das saídas; é o principal gargalo observado |
| Banco | SQLite (testes) ou Oracle 12c+ (produção) |

## 3. Instalação

```powershell
cd <pasta do projeto>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 4. Configuração: TUDO em `config.toml`

1. Copie `config.exemplo.toml` para `config.toml` (na raiz do projeto).
2. Ajuste as seções. Cada chave está comentada no próprio arquivo.
3. Confira a configuração em uso: `python scripts\config.py`

| Seção | Chaves | Observação |
|---|---|---|
| `[banco]` | `tipo`, `sqlite_arquivo`, `oracle_dsn`, `oracle_usuario`, `url` | `tipo = 'oracle'` para produção |
| `[pastas]` | `entrada`, `dados`, `logs`, `subpasta_pdf24` | `dados` = onde ficam `trabalho\`, `saida\`, `quarentena\` |
| `[compressao]` | `receita`, `dpi`, `qualidade_jpeg` | Decisão validada: 2 / 150 / 80 |
| `[validacao]` | `amostra`, `dpi_leitura`, `max_parcelas` | `amostra = 1` valida os códigos de todos os carnês |
| `[lote]` | `paralelo`, `aviso_a_cada_s` | `paralelo` limitado por memória e rede |

**Prioridade:** linha de comando > variável de ambiente > `config.toml` > padrão do código.

**Variáveis de ambiente:** qualquer chave pode ser sobrescrita com `IPTU_<SEÇÃO>_<CHAVE>`
(ex.: `IPTU_LOTE_PARALELO=5`, `IPTU_BANCO_TIPO=oracle`). Útil em servidores e agendadores.

**Senhas nunca vão no arquivo.** A senha do Oracle vem da variável de ambiente
`IPTU_BANCO_ORACLE_SENHA`; se não estiver definida, é pedida no terminal ao iniciar
(numa execução agendada, a variável precisa estar definida, idealmente vinda de um cofre de senhas).

**Caminhos do Windows no TOML:** use aspas simples: `entrada = 'S:\Modelo_IPTU'`.

### Regras importantes

- O **SQLite nunca** deve ficar em pasta de rede (risco de corrupção com gravações simultâneas).
- As pastas `trabalho\`, `saida\` e `quarentena\` ficam **sempre juntas** (mesmo disco), para que
  a publicação seja uma renomeação atômica.
- A pasta de saídas não deve ficar **dentro** da pasta de entrada (há proteção no código,
  mas a separação é mais clara).
- Se a receita mudar (`dpi`, `qualidade_jpeg`), o sufixo dos arquivos muda junto
  (ex.: `-r2-200dpi-q80.pdf`); ao publicar a nova versão, as versões antigas do mesmo
  original são removidas de `saida\` e `quarentena\`.

## 5. Banco Oracle

1. O DBA executa `sql/oracle_01_usuario.sql` (usuário `IPTU_COMPRESSAO`, menor privilégio).
2. As tabelas são criadas pela ferramenta na primeira conexão (requer `CREATE TABLE`),
   **ou** pelo DBA com `sql/oracle_02_tabelas.sql` (gerado de `python scripts\db.py --ddl`).
3. Configure `[banco]` no `config.toml` e defina `IPTU_BANCO_ORACLE_SENHA`.
4. Teste: `python scripts\db.py` → deve mostrar `Banco real OK (oracle)`.

Mudanças de esquema: `scripts/db.py`, função `migrar()` (ALTER TABLE para colunas novas).
Em evoluções maiores, recomenda-se adotar o Alembic.

## 6. Execução

```powershell
# o que seria feito (não processa nada)
python scripts\processar_lote.py --listar

# processar (pasta e opções vêm do config.toml)
python scripts\processar_lote.py

# processar só um CDD, sobrescrevendo o paralelismo
python scripts\processar_lote.py "S:\Modelo_IPTU\CDD_Intermunicipal" --paralelo 2
```

| Opção | Efeito |
|---|---|
| `--listar` | Mostra a decisão para cada arquivo, sem processar |
| `--forcar` | Ignora o banco e processa tudo de novo |
| `--limite N` | Processa só os N primeiros arquivos (testes) |
| `--paralelo N`, `--amostra N`, `--dados PASTA` | Sobrescrevem o `config.toml` |

**Retomada:** rodar o mesmo comando de novo continua de onde parou (pula o que está PRONTO,
refaz o interrompido ou com ERRO, só valida o que foi comprimido e não validado).

**Trava:** só um lote por pasta de dados (`<dados>\lote.lock`). Se sobrar após uma queda de
energia, apagar o arquivo.

**Códigos de saída** (úteis para agendadores): `0` = tudo aprovado; `1` = houve ERRO ou
REPROVADO, ou erro no `config.toml`; `2` = não iniciou (sem permissão na pasta de dados ou
outro lote rodando); `130` = interrompido.

## 7. Acompanhamento e auditoria

```powershell
python scripts\consultar.py            # lista as consultas
python scripts\consultar.py resumo     # situação geral
python scripts\consultar.py prontos    # o que pode ir para a gráfica
python scripts\consultar.py problemas  # o que falhou e onde
python scripts\consultar.py tempos     # tempo por arquivo (compressão, validação, total)
python scripts\consultar.py lotes      # tempo de cada lote
```

As consultas ficam em `sql/consultas_auditoria.sql` (SQL padrão; rodam também no SQL Developer).
Detalhes: `docs/consultas.md`.

## 8. Arquivos do projeto

| Arquivo | Papel |
|---|---|
| `scripts/config.py` | Configuração central (config.toml + variáveis de ambiente) |
| `scripts/db.py` | Modelo do banco, conexão, migração, DDL Oracle (`--ddl`) |
| `scripts/comprimir_um.py` | Compressão de um arquivo + registro |
| `scripts/validar.py` | Validação de um arquivo + registro |
| `scripts/processar_lote.py` | Orquestração: pasta inteira, paralelismo, retomada, quarentena |
| `scripts/consultar.py` | Consultas de auditoria |
| `scripts/diagnostico_pdf.py` | Diagnóstico (somente leitura) da estrutura de um PDF |
| `sql/` | Consultas de auditoria e scripts Oracle |
| `docs/` | Documentação |
| `config.exemplo.toml` | Modelo de configuração |
| `requirements.txt` | Dependências com versões validadas |

## 9. Desempenho observado no piloto

| Medida | Valor |
|---|---|
| Arquivo de 251 MB, sozinho | compressão ≈ 106 s + validação ≈ 200 s |
| 3 arquivos de ≈ 250 MB em paralelo (rede) | ≈ 9,5 min cada (contenção de recursos); ganho ≈ 1,6× sobre sequencial |
| Tamanho final vs PDF24 | 24,2 MB vs 24,7 MB (arquivo de 500 carnês) |

A validação é mais da metade do tempo. Para reduzir o tempo total: `amostra` > 1 (os
códigos são lidos por amostragem; texto e linhas digitáveis continuam 100%) e/ou executar
a ferramenta em servidor próximo ao armazenamento.
