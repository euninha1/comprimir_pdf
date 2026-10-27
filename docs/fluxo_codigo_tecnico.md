# O que o código faz, passo a passo

Dois scripts rodam em sequência para cada PDF de carnês:

1. `comprimir_um.py`: **comprime** e registra.
2. `validar.py`: **confere** se nada se perdeu e registra.

---

## 1. comprimir_um.py

```
 PDF original (S:\...)
      │
      ▼
 ① lê a "impressão digital" (SHA-256) e os dados do nome/pasta
 ② registra no banco: "começou" (PROCESSANDO)
      │
      ▼
 ③ remove as cópias repetidas de imagens     (sem perda)
 ④ reduz as imagens decorativas a 150 DPI    (JPEG q80)
      │
      ▼
 ⑤ grava o PDF comprimido em data\saida\     (original intacto)
 ⑥ confere o número de páginas
 ⑦ registra no banco: "terminou" (OK ou ERRO) + tamanhos, hash, tempo
```

| Passo | O que o código faz | Função | Por quê |
|---|---|---|---|
| ① | Calcula o **SHA-256** do original (lendo em blocos de 1 MB); tira o **CDD** do nome da pasta e os **CEPs e o lote** do nome do arquivo; lê o tamanho do arquivo do PDF24, se informado | `sha256_arquivo`, `metadados` | Identificar o arquivo de forma única e permitir relatórios por CDD |
| ② | Grava no banco uma `execucao` e um `arquivo` com status **PROCESSANDO** | `db.conectar` | Se o PC desligar no meio, o banco mostra o que ficou pela metade |
| ③ | Abre o PDF e o regrava **em memória** removendo objetos sem uso e **juntando objetos idênticos** (as ~6.000 cópias de imagem viram ~18); comprime sem perda o que estava sem compressão; agrupa objetos pequenos | `deduplicar` (`garbage=4`, `deflate`, `use_objstms`) | É a causa de 88% do tamanho; resolvido sem perder nenhum pixel |
| ④ | Para cada imagem **distinta**: descobre o maior tamanho em que ela aparece impressa; se estiver acima de 150 DPI, **reduz os pixels** na proporção certa, converte para **JPEG qualidade 80** e troca o conteúdo **no próprio objeto** (vale para todas as páginas que a usam). Não mexe em máscaras, imagens de 1 bit nem faixas de 1×1 pixel | `reduzir_imagens` | Igualar/superar o tamanho do PDF24. Código de barras e QR são vetores: não são tocados |
| ⑤ | Grava o resultado em `data\saida\<nome>-r2-150dpi-q80.pdf` | - | O original nunca é alterado |
| ⑥ | Abre original e comprimido e compara o **número de páginas** | - | Conferência mínima; a completa é o `validar.py` |
| ⑦ | Calcula o SHA-256 do comprimido e atualiza o registro: tamanho, páginas, carnês, tempo, pico de memória, status **OK** ou **ERRO** (com a mensagem) | - | Evidência do que foi feito; o hash liga a compressão à validação |

**Se der erro em qualquer passo de ③ a ⑥:** o erro é capturado, o registro fica com status
**ERRO** e a mensagem do problema; o script termina com código 1.

---

## 2. validar.py

```
 PDF original  +  PDF comprimido
      │                 │
      └──────┬──────────┘
             ▼
 ① confere número de páginas (iguais e pares)
             │
             ▼   para cada página
 ② compara o TEXTO
 ③ compara as LINHAS DIGITÁVEIS
 ④ confere a REGRA DO CARNÊ (frente 1 / verso 1 a 12)
 ⑤ "fotografa" a página e LÊ os códigos de barras e QR Codes (nos dois)
 ⑥ confere se as BARRAS batem com a LINHA DIGITÁVEL impressa
             │
             ▼
 ⑦ APROVADO (nenhum problema) ou REPROVADO (lista de problemas)
 ⑧ registra no banco, ligado ao arquivo pelo SHA-256 do comprimido
```

| Passo | O que o código faz | Por quê |
|---|---|---|
| ① | Compara o número de páginas dos dois arquivos e verifica se o original tem número par | Página perdida = contribuinte sem carnê; ímpar = carnê sem verso |
| ② | Extrai o texto de cada página dos dois arquivos, normaliza os espaços e compara | Nome, valores, datas: qualquer caractere diferente reprova |
| ③ | Procura as linhas digitáveis (47/48 dígitos) em cada página e compara as listas | É o dado de pagamento; reportado à parte para ficar claro |
| ④ | No original: página ímpar (frente) deve ter **1** linha digitável; página par (verso), **1 a 12** | Pega carnê que já veio com defeito do sistema gerador |
| ⑤ | Renderiza a página em imagem cinza a 200 DPI e usa o leitor **zxing-cpp** para ler todos os códigos **ITF** e **QR Code**; compara o que foi lido nos dois arquivos | Prova a **legibilidade**: código borrado o leitor não lê |
| ⑥ | Converte cada linha digitável de 48 dígitos no código de 44 (tira os 4 dígitos verificadores) e compara com o que foi lido nas barras | Barras e linha impressa têm que representar a mesma cobrança |
| ⑦ | Se houve qualquer problema: **REPROVADO** e lista (página, carnê, lado, tipo); senão **APROVADO** | Regra: uma falha reprova o arquivo inteiro |
| ⑧ | Calcula o SHA-256 do comprimido, encontra no banco o `arquivo` com o mesmo hash e grava a `validacao` e cada `problema` | Liga a validação exatamente àquele arquivo, byte a byte |

**Opções:** `--amostra N` lê os códigos (passos ⑤ e ⑥) em 1 a cada N carnês (mais rápido);
os passos ① a ④ são sempre feitos em 100% das páginas. `--dpi` muda a resolução da leitura.

---

## Resultado no banco depois dos dois scripts

```
execucao ──► arquivo (status OK) ──► validacao (aprovado = 1) ──► problema (nenhum)
```

**Pronto para a gráfica** = `arquivo.status = 'OK'` **e** `validacao.aprovado = 1`.
