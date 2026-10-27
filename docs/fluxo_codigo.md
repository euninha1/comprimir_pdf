# Fluxo do código: explicação simples

Imagine uma gráfica com **dois funcionários**: um que **arruma** o arquivo e outro que
**confere** o trabalho do primeiro. E um **caderno** onde os dois anotam tudo.

| Na analogia | No projeto |
|---|---|
| Funcionário que arruma | `comprimir_um.py` |
| Funcionário que confere | `validar.py` |
| Caderno | Banco de dados de auditoria |

> Versão técnica, com os nomes das funções: `docs/fluxo_codigo_tecnico.md`

---

## Primeiro funcionário: o que comprime (`comprimir_um.py`)

**1. Pega o arquivo e anota no caderno.**
Pega o PDF de carnês e escreve: "comecei o arquivo tal, da pasta tal". Também tira uma
**impressão digital** do arquivo, um código único que identifica aquele arquivo e nenhum outro.

**2. Joga fora as cópias repetidas.**
Dentro do PDF, o brasão, as artes e os logos dos bancos estavam **copiados centenas de
vezes**: uma cópia para cada carnê. É como um álbum em que a mesma foto foi impressa 500
vezes. O funcionário guarda **uma** foto só e põe um "veja a foto da página 1" em todas as
outras. O arquivo fica muito menor e **nada muda no que se vê**.

**3. Diminui um pouco a resolução das imagens de enfeite.**
O brasão, as artes e os logos estavam com qualidade muito maior do que a impressora consegue
mostrar. Ele ajusta para uma qualidade **boa para impressão**, a mesma que o PDF24 usa.
**Não encosta** em textos, valores, linhas digitáveis, códigos de barras nem QR Code.

**4. Salva a versão nova em outra pasta.**
O arquivo original **fica intacto**. A versão comprimida vai para uma pasta separada.

**5. Anota no caderno como foi.**
"Terminei. Tinha 251 MB, ficou com 24 MB, levou 2 minutos." Se algo deu errado, anota
**o que deu errado**, em vez de deixar passar.

---

## Segundo funcionário: o que confere (`validar.py`)

Coloca o **original** e o **comprimido** lado a lado e confere **página por página**:

**1. Contou as páginas?**
As duas versões têm a mesma quantidade? Nenhuma sumiu?

**2. O texto é o mesmo?**
Nomes, endereços, valores, datas: tudo igual, letra por letra?

**3. Os números para pagar são os mesmos?**
As linhas digitáveis estão idênticas?

**4. Cada carnê está completo?**
A frente tem a cota única e o verso tem as parcelas?

**5. Os códigos funcionam?**
Ele **"tira uma foto" da página** e passa um **leitor de código de barras**, como o da
lotérica, em todos os códigos de barras e QR Codes, nas duas versões. Se o leitor consegue
ler tudo e o resultado é igual, os códigos estão perfeitos.

**6. O código de barras bate com o número impresso?**
O que o leitor leu nas barras tem que ser o mesmo número escrito na linha digitável.

**Veredito:**
- **Tudo certo:** **APROVADO**.
- **Um único problema**, em qualquer página: **REPROVADO**, com a anotação de exatamente
  onde (página, carnê, frente ou verso) e qual foi o problema.

Por fim, anota o resultado no caderno, **grudado na anotação do primeiro funcionário**.
Para não confundir os arquivos, usa a **impressão digital**: só aceita conferir o arquivo
que foi mesmo produzido pelo primeiro funcionário.

---

## O caderno (banco de dados)

Guarda a **história de cada arquivo**. Exemplo:

> Arquivo `49010000_49027415_1`, pasta do CDD Aracaju 49010000:
> comprimido de 251 MB para 24 MB → conferido: 500 carnês, 3.562 códigos de barras e
> 500 QR Codes lidos, nenhum problema → **APROVADO**.

Com o caderno dá para responder a qualquer momento:

- O que já está pronto para a gráfica?
- O que deu problema, e onde?
- Quanto espaço economizamos?
- O que ainda falta fazer?

---