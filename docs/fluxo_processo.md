# Fluxo do processo: compressão dos carnês de IPTU

```
 [0] diagnostico_pdf.py   (só quando o layout do carnê mudar)
          │  entender o PDF antes de mexer
          ▼
 [1] db.py                (uma vez: cria o banco de auditoria)
          │
          ▼
 ┌──────────────── para cada PDF de carnês ────────────────┐
 │ [2] comprimir_um.py ──► PDF comprimido em data\saida\     │
 │          │               + registro em execucao/arquivo  │
 │          ▼                                               │
 │ [3] validar.py ───────► APROVADO / REPROVADO             │
 │                          + registro em validacao/problema│
 └──────────────────────────────────────────────────────────┘
          │
          ▼
 [4] consultas SQL        (o que está pronto? quanto reduziu? o que falhou?)
          │
          ▼
 [5] envio à gráfica      (fora do escopo do projeto: só arquivos APROVADOS)
```

---

## [0] diagnostico_pdf.py: entender o PDF

- **Quando:** só no início do projeto, ou quando o layout do carnê mudar (por exemplo,
  se o carnê do próximo ano trouxer QR Code PIX ou artes novas).
- **O que faz:** abre o PDF **só para leitura** e mostra o que ocupa espaço: imagens,
  cópias repetidas, resolução, fontes, vetores.
- **Por quê:** não se otimiza o que não se mediu. Foi esse diagnóstico que mostrou que
  88% do arquivo eram **cópias da mesma imagem** e que o código de barras e o QR Code são
  **vetores** (não são afetados pela compressão). Todas as decisões vieram daqui.
- **Grava no banco?** Não. É análise.

## [1] db.py: preparar o banco

- **Quando:** uma vez (ou sempre que mudar de banco: SQLite para Oracle).
- **O que faz:** conecta no banco e cria as 4 tabelas que faltarem
  (`execucao`, `arquivo`, `validacao`, `problema`).
- **Por quê:** sem banco, o resultado de cada execução some ao fechar o terminal.
  Com banco, tudo vira **evidência consultável** para auditoria e para retomar o trabalho.
- **Grava no banco?** Só cria a estrutura (tabelas vazias).

## [2] comprimir_um.py: comprimir

- **Quando:** para cada PDF de carnês.
- **O que faz:**
  1. registra no banco que começou (status `PROCESSANDO`);
  2. **remove as cópias** de imagens repetidas (sem perda);
  3. **reduz as imagens decorativas** (brasão, artes, logos) para 150 DPI, JPEG q80;
  4. grava o PDF comprimido em `data\saida\` (o original **nunca** é alterado);
  5. atualiza o registro: tamanhos, hashes, páginas, tempo e status `OK` ou `ERRO`.
- **Por quê:** é o objetivo do projeto: substituir o PDF24 manual.
  Resultado: 251 MB para 24,2 MB (PDF24: 24,7 MB), com imagens de melhor qualidade.
- **Grava no banco?** Sim: tabelas `execucao` e `arquivo`.

## [3] validar.py: provar que nada se perdeu

- **Quando:** logo depois de comprimir cada PDF.
- **O que faz:** compara original × comprimido, página por página, em 6 critérios
  (páginas, texto, linhas digitáveis, regra do carnê, leitura dos códigos de barras e
  QR Code, barras × linha digitável). Ver `docs/criterios_validacao.md`.
- **Por quê:** comprimir menor não basta: é preciso **provar** que nenhuma informação
  de pagamento foi alterada ou ficou ilegível. "Achamos que está bom" vira
  "3.562 códigos de barras e 500 QR Codes lidos, 0 divergências".
- **Grava no banco?** Sim: tabelas `validacao` e `problema`, ligadas ao registro do
  arquivo pelo **SHA-256 do comprimido** (garante que foi exatamente aquele arquivo).

## [4] Consultas SQL: acompanhar e auditar (próximo passo, 5.4)

- **Quando:** durante e depois do processamento.
- **O que faz:** responde perguntas como:
  - quais arquivos estão **prontos** (comprimidos e aprovados)?
  - quais **falharam** e por quê?
  - quanto **reduzimos** no total e por CDD?
  - o que ainda **falta** processar?
- **Por quê:** com ~440 arquivos, ninguém confere no olho. O banco responde em segundos
  e serve de evidência para gestão e auditoria.

## [5] Envio à gráfica (fora do escopo)

- **Regra:** só vai para a gráfica o arquivo com `arquivo.status = 'OK'` **e**
  `validacao.aprovado = 1`.

---

## Próxima etapa do projeto (Fase 6)

Hoje os passos [2] e [3] são rodados **à mão, um arquivo por vez**.
Na Fase 6, um único comando vai fazer [2] + [3] para **todos os PDFs de uma pasta**,
em paralelo, retomando de onde parou se o PC desligar, e separando em quarentena o
que der erro.
