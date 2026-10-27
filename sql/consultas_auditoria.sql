-- Consultas de auditoria da compressão dos carnês de IPTU (Fase 5.4).
-- SQL padrão: funciona igual no SQLite e no Oracle.
-- Cada consulta começa com "-- nome:", usado pelo scripts/consultar.py.
--
-- Conceito-chave: o mesmo PDF pode ter sido comprimido mais de uma vez (testes,
-- reprocessamento). Por isso as consultas olham sempre a ÚLTIMA compressão de cada
-- arquivo (maior id por cdd + nome) e a ÚLTIMA validação de cada compressão.


-- nome: situacao
-- Situação atual de cada arquivo: PRONTO, REPROVADO, SEM_VALIDACAO, ERRO ou PROCESSANDO.
SELECT a.cdd,
       a.nome,
       CASE
         WHEN a.status = 'PROCESSANDO' THEN 'PROCESSANDO'
         WHEN a.status = 'ERRO'        THEN 'ERRO'
         WHEN v.id IS NULL             THEN 'SEM_VALIDACAO'
         WHEN v.aprovado = 1           THEN 'PRONTO'
         ELSE 'REPROVADO'
       END                                                         AS situacao,
       ROUND(a.bytes_original   / 1048576.0, 1)                    AS mb_original,
       ROUND(a.bytes_comprimido / 1048576.0, 1)                    AS mb_comprimido,
       a.carnes,
       a.processado_em
FROM arquivo a
LEFT JOIN validacao v
       ON v.id = (SELECT MAX(v2.id) FROM validacao v2 WHERE v2.arquivo_id = a.id)
WHERE a.id = (SELECT MAX(a2.id) FROM arquivo a2 WHERE a2.cdd = a.cdd AND a2.nome = a.nome)
ORDER BY a.cdd, a.nome;


-- nome: resumo
-- Quantos arquivos em cada situação (a "foto" geral do processo).
SELECT situacao, COUNT(*) AS arquivos, SUM(carnes) AS carnes
FROM (
  SELECT a.carnes,
         CASE
           WHEN a.status = 'PROCESSANDO' THEN 'PROCESSANDO'
           WHEN a.status = 'ERRO'        THEN 'ERRO'
           WHEN v.id IS NULL             THEN 'SEM_VALIDACAO'
           WHEN v.aprovado = 1           THEN 'PRONTO'
           ELSE 'REPROVADO'
         END AS situacao
  FROM arquivo a
  LEFT JOIN validacao v
         ON v.id = (SELECT MAX(v2.id) FROM validacao v2 WHERE v2.arquivo_id = a.id)
  WHERE a.id = (SELECT MAX(a2.id) FROM arquivo a2 WHERE a2.cdd = a.cdd AND a2.nome = a.nome)
) t
GROUP BY situacao
ORDER BY situacao;


-- nome: prontos
-- Arquivos liberados para a gráfica: compressão OK E última validação aprovada.
SELECT a.cdd,
       a.nome,
       ROUND(a.bytes_comprimido / 1048576.0, 1) AS mb_comprimido,
       a.carnes,
       v.validada_em,
       SUBSTR(a.sha256_comprimido, 1, 12)       AS hash_inicio
FROM arquivo a
JOIN validacao v
  ON v.id = (SELECT MAX(v2.id) FROM validacao v2 WHERE v2.arquivo_id = a.id)
WHERE a.status = 'OK'
  AND v.aprovado = 1
  AND a.id = (SELECT MAX(a2.id) FROM arquivo a2 WHERE a2.cdd = a.cdd AND a2.nome = a.nome)
ORDER BY a.cdd, a.nome;


-- nome: problemas
-- Erros de compressão e problemas de validação ATUAIS (só a última compressão e a última validação de cada arquivo), com o local exato.
SELECT a.cdd, a.nome, 'COMPRESSAO' AS etapa, NULL AS pagina, NULL AS lado,
       'ERRO' AS tipo, a.mensagem AS descricao
FROM arquivo a
WHERE a.status = 'ERRO'
  AND a.id = (SELECT MAX(a2.id) FROM arquivo a2 WHERE a2.cdd = a.cdd AND a2.nome = a.nome)
UNION ALL
SELECT a.cdd, a.nome, 'VALIDACAO', p.pagina, p.lado, p.tipo, p.descricao
FROM problema p
JOIN validacao v ON v.id = p.validacao_id
JOIN arquivo a   ON a.id = v.arquivo_id
WHERE a.id = (SELECT MAX(a2.id) FROM arquivo a2 WHERE a2.cdd = a.cdd AND a2.nome = a.nome)
  AND v.id = (SELECT MAX(v2.id) FROM validacao v2 WHERE v2.arquivo_id = a.id)
ORDER BY 1, 2, 4;


-- nome: reducao_cdd
-- Redução por CDD e comparação com o PDF24 (só compressões OK, última de cada arquivo).
SELECT a.cdd,
       COUNT(*)                                                     AS arquivos,
       SUM(a.carnes)                                                AS carnes,
       ROUND(SUM(a.bytes_original)   / 1073741824.0, 2)             AS gb_original,
       ROUND(SUM(a.bytes_comprimido) / 1073741824.0, 2)             AS gb_comprimido,
       ROUND(100 * (1 - SUM(a.bytes_comprimido) * 1.0 / SUM(a.bytes_original)), 1) AS reducao_pct,
       ROUND(SUM(a.bytes_pdf24)      / 1073741824.0, 2)             AS gb_pdf24
FROM arquivo a
WHERE a.status = 'OK'
  AND a.id = (SELECT MAX(a2.id) FROM arquivo a2 WHERE a2.cdd = a.cdd AND a2.nome = a.nome)
GROUP BY a.cdd
ORDER BY a.cdd;


-- nome: travados
-- Compressões que começaram e não terminaram e ainda não foram refeitas (PC desligou, Ctrl+C...).
SELECT a.cdd, a.nome, a.processado_em, e.maquina, e.usuario
FROM arquivo a
JOIN execucao e ON e.id = a.execucao_id
WHERE a.status = 'PROCESSANDO'
  AND a.id = (SELECT MAX(a2.id) FROM arquivo a2 WHERE a2.cdd = a.cdd AND a2.nome = a.nome)
ORDER BY a.processado_em;


-- nome: desempenho
-- Tempo e memória por arquivo (compressão) e por validação.
SELECT 'compressao'                    AS etapa,
       COUNT(*)                        AS quantidade,
       ROUND(AVG(a.tempo_s), 1)        AS tempo_medio_s,
       ROUND(MAX(a.tempo_s), 1)        AS tempo_max_s,
       ROUND(MAX(a.pico_memoria_mb))   AS memoria_max_mb
FROM arquivo a
WHERE a.status = 'OK'
UNION ALL
SELECT 'validacao',
       COUNT(*),
       ROUND(AVG(v.tempo_s), 1),
       ROUND(MAX(v.tempo_s), 1),
       NULL
FROM validacao v;


-- nome: tempos
-- Tempo de CADA ARQUIVO em segundos: compressão, validação e total (última de cada arquivo).
SELECT a.cdd,
       a.nome,
       a.carnes,
       ROUND(a.tempo_s, 1)                            AS compressao_s,
       ROUND(v.tempo_s, 1)                            AS validacao_s,
       ROUND(a.tempo_s + COALESCE(v.tempo_s, 0), 1)   AS total_s,
       e.lote_id
FROM arquivo a
JOIN execucao e ON e.id = a.execucao_id
LEFT JOIN validacao v
       ON v.id = (SELECT MAX(v2.id) FROM validacao v2 WHERE v2.arquivo_id = a.id)
WHERE a.status = 'OK'
  AND a.id = (SELECT MAX(a2.id) FROM arquivo a2 WHERE a2.cdd = a.cdd AND a2.nome = a.nome)
ORDER BY total_s DESC;


-- nome: lotes
-- Tempo de CADA LOTE em segundos (relógio de início ao fim) e a soma dos tempos dos arquivos.
SELECT l.id                                   AS lote,
       l.iniciado_em,
       l.status,
       l.paralelo,
       l.arquivos_a_fazer,
       l.prontos, l.reprovados, l.erros, l.pulados,
       ROUND(l.duracao_s, 0)                  AS duracao_lote_s,
       ROUND((SELECT SUM(a.tempo_s + COALESCE(v.tempo_s, 0))
              FROM execucao e
              JOIN arquivo a ON a.execucao_id = e.id
              LEFT JOIN validacao v
                     ON v.id = (SELECT MAX(v2.id) FROM validacao v2 WHERE v2.arquivo_id = a.id)
              WHERE e.lote_id = l.id), 0)     AS soma_arquivos_s
FROM lote l
ORDER BY l.id DESC;
