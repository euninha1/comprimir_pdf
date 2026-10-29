# Consultas de auditoria (`scripts/consultar.py`)

Rodar no PowerShell, com o `(.venv)` ativo, dentro de `C:\projetos\comprimir_pdf`.
As consultas ficam em `sql/consultas_auditoria.sql` (SQL padrão: funciona no SQLite e no Oracle).

| Comando | O que mostra |
|---|---|
| `python scripts\consultar.py` | Lista as consultas disponíveis |
| `python scripts\consultar.py <nome>` | Roda uma consulta |
| `python scripts\consultar.py <nome1> <nome2>` | Roda várias |
| `python scripts\consultar.py todas` | Roda todas |

As consultas consideram sempre a **última** compressão de cada arquivo e a **última**
validação de cada compressão (reprocessamentos não duplicam o resultado).

---

## As situações possíveis de um arquivo

| Situação | Significado | O que fazer |
|---|---|---|
| **PRONTO** | Comprimido **e** aprovado na validação | Pode ir para a gráfica |
| **REPROVADO** | Comprimido, mas a validação encontrou problema | Ver `problemas`; não enviar |
| **SEM_VALIDACAO** | Comprimido, mas ainda não validado | Rodar a validação |
| **ERRO** | A compressão falhou (ex.: PDF corrompido) | Ver `problemas`; verificar o original |
| **PROCESSANDO** | Começou e não terminou (interrompido, travou) | Ver `travados`; reprocessar |

---

## 1. `resumo`: a foto geral

```powershell
python scripts\consultar.py resumo
```

- **Responde:** quantos arquivos (e carnês) estão em cada situação.
- **Quando usar:** para acompanhar o andamento do lote; é a primeira consulta do dia.
- **Colunas:** `situacao`, `arquivos`, `carnes`.

## 2. `situacao`: arquivo por arquivo

```powershell
python scripts\consultar.py situacao
```

- **Responde:** a situação de cada arquivo, com tamanho antes e depois.
- **Quando usar:** para ver exatamente quais arquivos estão em cada situação.
- **Colunas:** `cdd`, `nome`, `situacao`, `mb_original`, `mb_comprimido`, `carnes`, `processado_em`.

## 3. `prontos`: o que pode ir para a gráfica

```powershell
python scripts\consultar.py prontos
```

- **Responde:** os arquivos comprimidos **e** aprovados na validação.
- **Quando usar:** antes de enviar à gráfica. **Só esta lista deve ser enviada.**
- **Colunas:** `cdd`, `nome`, `mb_comprimido`, `carnes`, `validada_em`, `hash_inicio`
  (início da impressão digital do arquivo, para conferir que é o mesmo que foi validado).

## 4. `problemas`: o que deu errado e onde

```powershell
python scripts\consultar.py problemas
```

- **Responde:** erros de compressão e problemas de validação, com página, lado e tipo.
- **Quando usar:** quando o `resumo` mostrar ERRO ou REPROVADO.
- **Colunas:** `cdd`, `nome`, `etapa` (COMPRESSAO ou VALIDACAO), `pagina`, `lado`
  (frente/verso), `tipo`, `descricao`.
- **Tipos de problema:** `ERRO` (compressão), `PAGINAS`, `TEXTO`, `LINHA_DIGITAVEL`,
  `REGRA_CARNE`, `CODIGO_LIDO`, `BARRAS_X_LINHA`, `REGIAO_CODIGO` (ver `docs/criterios_validacao.md`).

## 5. `reducao_cdd`: quanto economizamos

```powershell
python scripts\consultar.py reducao_cdd
```

- **Responde:** tamanho antes, depois e no PDF24, por CDD, e a % de redução.
- **Quando usar:** para relatórios e apresentações para a gestão.
- **Colunas:** `cdd`, `arquivos`, `carnes`, `gb_original`, `gb_comprimido`, `reducao_pct`, `gb_pdf24`.

## 6. `travados`: o que ficou pela metade

```powershell
python scripts\consultar.py travados
```

- **Responde:** arquivos que começaram a ser comprimidos e não terminaram.
- **Quando usar:** depois de uma interrupção (Ctrl+C, PC desligou, queda de energia).
- **Colunas:** `cdd`, `nome`, `processado_em`, `maquina`, `usuario`.

## 7. `desempenho`: tempo e memória

```powershell
python scripts\consultar.py desempenho
```

- **Responde:** tempo médio e máximo de compressão e de validação; pico de memória.
- **Quando usar:** para estimar quanto tempo o lote completo vai levar e quantos
  arquivos dá para processar em paralelo.
- **Colunas:** `etapa`, `quantidade`, `tempo_medio_s`, `tempo_max_s`, `memoria_max_mb`.

---

## Rotina sugerida

| Momento | Consultas |
|---|---|
| Durante o lote | `resumo` |
| Depois de uma interrupção | `travados`, `resumo` |
| Ao terminar o lote | `resumo`, `problemas` |
| Antes de enviar à gráfica | `prontos` |
| Relatório para a gestão | `reducao_cdd`, `desempenho` |
