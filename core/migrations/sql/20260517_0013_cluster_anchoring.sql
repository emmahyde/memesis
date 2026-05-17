-- Cluster anchoring: a memory may belong to a named cluster.
-- When retrieval surfaces any cluster member, its siblings are pulled into the
-- candidate pool (1-hop expansion) so a coherent cluster surfaces together.
-- See core/graph.py:expand_clusters and core/retrieval.py:_crystallized_hybrid.
ALTER TABLE memories ADD COLUMN cluster TEXT;
CREATE INDEX IF NOT EXISTS idx_memories_cluster ON memories (cluster);
