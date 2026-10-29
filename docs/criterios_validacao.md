# Critérios de validação da compressão dos carnês de IPTU

Script: `scripts/validar.py`
Compara o PDF **original** com o PDF **comprimido**, página por página.

**Regra geral:** a compressão só é considerada bem-sucedida (**APROVADO**) se o arquivo
passar em **todos** os critérios abaixo, em **todas** as páginas.
Uma única falha em qualquer página reprova o arquivo inteiro (**REPROVADO**).

---

## Critério 1: mesmo número de páginas

- **O que verifica:** o comprimido tem exatamente o mesmo número de páginas do original,
  e o original tem um número **par** de páginas.
- **Por quê:** cada página é parte de um carnê (2 páginas por carnê: frente e verso).
  Uma página a menos significa um contribuinte sem carnê ou um carnê sem as parcelas.
  Número ímpar indica carnê incompleto já na origem.

## Critério 2: texto idêntico em cada página

- **O que verifica:** todo o texto de cada página do comprimido é igual ao da página
  correspondente no original (diferenças só de espaçamento são ignoradas).
- **Por quê:** o texto contém as informações do contribuinte e do imposto: nome,
  endereço, inscrição, valores, vencimentos. Qualquer letra ou número alterado é um
  documento fiscal errado.

## Critério 3: linhas digitáveis idênticas em cada página

- **O que verifica:** as linhas digitáveis (sequências de 47 ou 48 dígitos) de cada
  página são iguais e na mesma ordem no original e no comprimido.
- **Por quê:** a linha digitável é o que o contribuinte digita no banco para pagar.
  Se mudar um dígito, o pagamento falha ou vai para a cobrança errada. É verificado à
  parte do texto para que o relatório aponte com clareza um problema no dado de pagamento.

## Critério 4: carnê completo (verificado no original)

- **O que verifica:**
  - páginas **ímpares** (frente): exatamente **1** linha digitável (cota única);
  - páginas **pares** (verso): de **1 a 12** linhas digitáveis (parcelas).
- **Por quê:** garante que cada carnê tem a cota única e ao menos uma parcela.
  Pega erros que **já vieram do sistema gerador**, antes da compressão: não adianta
  comprimir bem um carnê que saiu errado na origem.

## Critério 5: códigos de barras e QR Code legíveis por software

- **O que verifica:** cada página é "fotografada" (200 DPI, tons de cinza) e um leitor
  automático (zxing-cpp) lê todos os códigos de barras (ITF) e QR Codes. Os códigos lidos
  no comprimido têm que ser **os mesmos e com o mesmo conteúdo** do original.
- **Por quê:** é o requisito de **legibilidade**. O código de barras é o que a lotérica e
  o caixa leem; o QR Code leva às informações do imóvel. Um código pode estar "presente"
  mas borrado a ponto de o leitor não conseguir ler; para o contribuinte, é como se não
  existisse. Comparar o texto não pega isso; só a leitura real pega.

## Critério 6: código de barras igual à linha digitável impressa

- **O que verifica:** para cada linha digitável de 48 dígitos, retiram-se os 4 dígitos
  verificadores (posições 12, 24, 36 e 48) e o resultado (44 dígitos) tem que ser
  **exatamente** o que o leitor leu nas barras.
- **Por quê:** a linha digitável e o código de barras representam a **mesma cobrança** de
  duas formas. Se divergirem, quem paga pelas barras e quem digita a linha pagam coisas
  diferentes. Também pega inconsistências que já vieram do sistema gerador.

## Critério 7: região de cada código idêntica ao original (pixel a pixel)

- **O que verifica:** para cada código de barras e QR Code lido no original, recorta-se a
  mesma região (com a zona de silêncio ao redor) na "foto" do original e na do comprimido.
  A **diferença média dos pixels** (escala de 0 a 255) tem que ficar dentro da tolerância
  (`tolerancia_regiao`, padrão **1,0**; 0 = idêntico).
- **Por quê:** o critério 5 só prova que um **software tolerante** consegue ler. Leitoras de
  caixa, lotéricas e apps de banco são bem mais exigentes: uma barra afinada ou borrada pelo
  JPEG ainda é lida pelo zxing, mas pode não ser lida no banco. A compressão **não deve tocar**
  nos códigos; por isso a exigência é que a região continue praticamente idêntica.
- **Referência medida:** barra gravada como imagem e reduzida para 150 DPI em JPEG →
  diferença ≈ **7,2** (lida pelo zxing, mas reprovada aqui). Barra vetorial ou imagem
  preservada → diferença **0,00**.
- **Proteção na compressão:** antes de reduzir qualquer imagem, `comprimir_um.py` tenta ler
  um ITF ou QR Code dentro dela; se encontrar, a imagem é **preservada sem alteração**.

---

## O que NÃO é verificado (e por quê)

| Não verificado | Motivo |
|---|---|
| Imagens decorativas (brasão, artes, logos dos bancos), fora das regiões dos códigos | Elas **devem** mudar: é onde a compressão reduz a resolução (150 DPI, JPEG q80). Qualidade conferida visualmente na Fase 3 |
| Posição do texto na página | Fora do escopo atual; melhoria futura |
| Dígitos verificadores da linha digitável (módulo 10/11) | Uma linha já errada na origem e igual nos dois arquivos passaria; melhoria futura |
| Conteúdo do QR Code (formato da URL / BR Code PIX) | Só se garante que é igual ao original |
| Conversão linha → código para boleto bancário de 47 dígitos | Os carnês usam arrecadação (48 dígitos) |

---

## Resultado de referência

Lote `49010000_49027415_1.pdf` (1.000 páginas, 500 carnês), receita 2 (150 DPI, JPEG q80):

- 1.000 páginas com texto idêntico
- 3.562 linhas digitáveis idênticas
- 3.562 códigos de barras lidos, todos batendo com as linhas digitáveis
- 500 QR Codes lidos (1 por carnê)
- **APROVADO**, em cerca de 200 a 250 s
