https://otto-pilot.cloud.justice.gc.ca/chat/id/a3cd8657-72b6-4b23-8521-7e503c2e6a42/
https://otto-pilot.cloud.justice.gc.ca/chat/id/19c4a4dd-e438-4650-a2cc-ad6363bcbec8/
https://otto-pilot.cloud.justice.gc.ca/chat/id/9474a06d-5347-44ce-89fb-d11cf616ab2d/


Before doing anything:
- find out what the LlamaIndex queries actually are!
- try benchmarking those on the main branch (have to load subset of laws first...)
- try benchmarking the queries below (vector and text separate, and maybe the combined one it suggested?)


1. load the subset of laws (so the index is smaller) from the laws-performance branch (this runs some SQL indexing stuff at the end)

2. how big is the embedding index? text indices?

SELECT
   pg_size_pretty(pg_relation_size('data_laws_lois___embedding_idx')) AS idx_on_disk,
   pg_size_pretty(pg_relation_size('data_laws_lois__chunk_text_idx')) AS chunk_text_idx,
   pg_size_pretty(pg_relation_size('data_laws_lois__'))               AS table_on_disk;

This will tell you how big the shared buffers and effective cache size need to be (at least as big as the index size)

3. Adjust local settings in docker-compose kind of like in kubernetes vectordb.yaml - this is just postgres.conf but through command params
          args:
            - "-c"
            - "shared_buffers=2GB" # adjust
            - "-c"
            - "work_mem=64MB"
            - "-c"
            - "effective_cache_size=6GB" # adjust


-- In PSQL, make sure the shared buffers are right:

SHOW shared_buffers;
SHOW work_mem;
SHOW effective_cache_size;

4. Benchmark stuff...
-- test text search with metadata filtering
EXPLAIN (ANALYZE, BUFFERS)
SELECT id
FROM data_laws_lois__
WHERE
   metadata_ ->> 'node_type' = 'chunk'
    -- AND metadata_ ->> 'doc_id'   = ANY('{…your list…}')
 AND text_search_tsv @@ plainto_tsquery('english','environment|law')
ORDER BY ts_rank(text_search_tsv,
                 plainto_tsquery('english','environment|law')
                ) DESC
LIMIT 100;

-- vector search -- needs helper function to create random vector
-- 1. Create helper function
CREATE OR REPLACE FUNCTION vector_random(dim INT)
  RETURNS vector AS $$
BEGIN
  RETURN (
    SELECT ARRAY(
      SELECT random()::float4
      FROM generate_series(1,dim)
    )::vector
  );
END;
$$ LANGUAGE plpgsql;

SET vector_hnsw.ef_search = 256;

-- 2. Run your test query (random vector generated ONCE per query)
EXPLAIN (ANALYZE, BUFFERS)
WITH q AS (
  SELECT vector_random(1536) AS query_emb
)
SELECT id, (embedding <=> q.query_emb) AS dist
FROM data_laws_lois__ d
CROSS JOIN q
ORDER BY dist
LIMIT 100;


-- If performance is bad, may need to prewarm... (see commands in loading_utils.py)

CREATE EXTENSION IF NOT EXISTS pg_prewarm;
SELECT pg_prewarm('data_laws_lois__','buffer');
SELECT pg_prewarm('data_laws_lois___embedding_idx','buffer');


-- If you do make changes to indexes, etc. make sure to vacuum analyze the table
VACUUM ANALYZE data_laws_lois__;



-- The code o4-mini suggested for replacing hybridfusionretriever...
-- This actually works horribly - it takes like 14s

-- 2. Run the optimized hybrid (dense + sparse) query
EXPLAIN (ANALYZE, BUFFERS)
WITH params AS (
  SELECT
    vector_random(1536)    AS query_emb,
    'environment|law'::text AS keywords
),
dense AS (
  SELECT
    d.id,
    (1 - (d.embedding <=> p.query_emb)::float) AS score_dense
  FROM data_laws_lois__ d
  CROSS JOIN params p
  WHERE d.metadata_ ->> 'node_type' = 'chunk'
  ORDER BY d.embedding <=> p.query_emb   -- THIS NOW USES YOUR HNSW INDEX
  LIMIT 100
),
sparse AS (
  SELECT
    d.id,
    ts_rank(d.text_search_tsv,
            to_tsquery('english', p.keywords)
    ) AS score_sparse
  FROM data_laws_lois__ d
  CROSS JOIN params p
  WHERE d.metadata_ ->> 'node_type' = 'chunk'
    AND d.text_search_tsv @@ to_tsquery('english', p.keywords)
  ORDER BY score_sparse DESC
  LIMIT 100
)
SELECT
  coalesce(d.id, s.id) AS id,
  coalesce(d.score_dense,  0) * 0.6
  + coalesce(s.score_sparse, 0) * 0.4   AS hybrid_score
FROM dense d
FULL OUTER JOIN sparse s USING (id)
ORDER BY hybrid_score DESC
LIMIT 50;


--- These are the commands from the loading_utils.py file (post-load reset_indexes command)
-- Drop any old generic GIN-on-JSONB index (if present)
DROP INDEX IF EXISTS data_laws_lois__metadata__idx;

-- Create BTREE expression index for node_type
CREATE INDEX IF NOT EXISTS data_laws_lois__node_type_idx
ON data_laws_lois__ ((metadata_ ->> 'node_type'));

-- Create BTREE expression index for doc_id
CREATE INDEX IF NOT EXISTS data_laws_lois__doc_id_idx
ON data_laws_lois__ ((metadata_ ->> 'doc_id'));

-- Drop old HNSW index (if present)
DROP INDEX IF EXISTS data_laws_lois___embedding_idx;

-- Create HNSW index on embedding (uncomment if you want HNSW)
-- CREATE INDEX data_laws_lois___embedding_idx
-- ON data_laws_lois__ USING hnsw (embedding vector_cosine_ops);

-- Refresh table statistics
VACUUM ANALYZE data_laws_lois__;

-- Ensure pg_prewarm is available
CREATE EXTENSION IF NOT EXISTS pg_prewarm;

-- Prewarm embedding index (uncomment if HNSW index was created)
SELECT pg_prewarm('data_laws_lois___embedding_idx','buffer');

-- Prewarm table heap
SELECT pg_prewarm('data_laws_lois__','buffer');
