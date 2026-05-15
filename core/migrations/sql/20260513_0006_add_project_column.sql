ALTER TABLE memories ADD COLUMN project TEXT;
ALTER TABLE observations ADD COLUMN project TEXT;
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_observations_project ON observations(project);
