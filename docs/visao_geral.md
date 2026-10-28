# Compressão automática dos carnês de IPTU: visão geral

## O problema

Todo ano a Secretaria da Fazenda gera cerca de **220 mil carnês de IPTU**, distribuídos
em cerca de **417 arquivos PDF** (cada arquivo tem uns 500 carnês de uma faixa de CEP).

Esses PDFs saem **enormes** (cerca de 250 MB cada, 307 GB no total). Antes de mandar para
a gráfica, alguém precisa **comprimir um por um no PDF24**, à mão: são cerca de 2 minutos
por arquivo, de **14 a 15 horas** de trabalho, e **ninguém confere** se algum carnê foi
afetado.

## Por que os PDFs são tão grandes

Investigamos por dentro dos arquivos e descobrimos que o sistema que gera os carnês
**repete as mesmas imagens** (brasão, artes da prefeitura, logos dos bancos) em cada
carnê. Em um arquivo, havia **6.159 cópias de apenas 18 imagens**. Ou seja, quase todo
o tamanho era **repetição**.

## A solução em uma frase

> Um programa que, sozinho, **comprime** cada arquivo, **confere** se nada de importante
> mudou, **separa** o que está pronto do que tem problema e **anota tudo** num banco de dados.

## Como funciona, passo a passo

Para cada arquivo de carnês:

1. **Lê o original** (que **nunca é alterado**).
2. **Comprime:**
   - guarda cada imagem repetida **uma vez só**, em vez de centenas de vezes;
   - ajusta a resolução das imagens de enfeite para a qualidade de impressão
     (a mesma que o PDF24 usa).
   - **Não mexe** em textos, valores, linhas digitáveis, códigos de barras nem QR Code.
3. **Confere**, comparando com o original, página por página:
   - as páginas são as mesmas?
   - o texto (nomes, valores, datas) está igual?
   - as linhas digitáveis estão iguais?
   - cada carnê tem a cota única e as parcelas?
   - **todos os códigos de barras e QR Codes continuam lendo?** (um leitor por software
     "fotografa" a página e lê os códigos, como o leitor da lotérica)
   - o código de barras bate com o número impresso?
4. **Separa:**
   - **aprovado**: vai para a pasta **saída** (pode ir para a gráfica);
   - **com problema**: vai para a pasta **quarentena**, com um arquivo explicando o motivo.
5. **Anota no banco de dados**: tamanho antes e depois, tempo, resultado da conferência e
   qualquer problema encontrado.

O programa processa **vários arquivos ao mesmo tempo** e, se for interrompido (PC
desligou, queda de energia), **continua de onde parou** ao ser executado de novo.

## Garantias de segurança

| Garantia | Como |
|---|---|
| O original nunca é alterado | A versão comprimida é gravada em outra pasta |
| Nada vai para a gráfica sem conferência | Só os arquivos **aprovados** chegam à pasta de saída |
| Nenhum carnê com código ilegível passa | Todos os códigos de barras e QR Codes são lidos por software |
| Tudo fica registrado | Cada arquivo tem o seu histórico no banco de dados |
| Dá para provar qual arquivo é qual | Cada arquivo tem uma "impressão digital" (hash) gravada |
| Sem risco para a LGPD | Os PDFs não saem da rede da prefeitura e o banco não guarda dados de contribuintes |
| Nada muda no sistema do IPTU | O programa trabalha depois, em cima dos PDFs já gerados |

## Resultados do teste com um arquivo real (500 carnês)

| | Original | PDF24 (manual) | **Nosso processo** |
|---|---:|---:|---:|
| Tamanho | 251 MB | 24,7 MB | **24,2 MB** |
| Qualidade das imagens | Original | Com manchas visíveis no zoom | **Mais nítida que o PDF24** |
| Códigos de barras e QR Code | - | Não conferidos | **3.562 códigos de barras e 500 QR Codes lidos, todos iguais ao original** |
| Trabalho manual | - | Um arquivo de cada vez | **Nenhum: roda sozinho** |
| Registro do que foi feito | - | Nenhum | **Banco de dados com o histórico** |

## Onde ficam as coisas

| O quê | Onde |
|---|---|
| Arquivos originais | `S:\Modelo_IPTU` (não são alterados) |
| Arquivos aprovados (para a gráfica) | pasta **saida** |
| Arquivos com problema + motivo | pasta **quarentena** |
| Histórico (banco de dados) e registros de execução | no computador que roda o processo |

## Etapas do projeto

| Etapa | Situação |
|---|---|
| Entender o problema e definir o que é "sucesso" | Concluída |
| Investigar por que os PDFs são grandes e o que o PDF24 faz | Concluída |
| Criar a compressão e comparar com o PDF24 | Concluída |
| Criar a conferência automática | Concluída |
| Registrar tudo em banco de dados | Concluída |
| Processar pastas inteiras, vários arquivos ao mesmo tempo | Concluída |
| **Piloto com os arquivos reais, aumentando a escala aos poucos** | **Em andamento** |
| Rodar todos os 417 arquivos (aguardando a pasta de rede para as saídas) | Próxima |
