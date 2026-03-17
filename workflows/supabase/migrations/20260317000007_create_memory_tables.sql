-- Migration 007: Memory Index Table
-- Indexes markdown memory files stored on disk for queryability.
-- Files are the primary store (human-readable, git-friendly).
-- This table is the index for fast lookups by project/agent/tags.

CREATE TABLE memory_index (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  file_path text NOT NULL UNIQUE,
  memory_type text NOT NULL CHECK (memory_type IN ('project', 'agent')),
  project_name text,
  agent_id text,
  tags text[] DEFAULT '{}',
  summary text NOT NULL,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_memory_project ON memory_index(project_name);
CREATE INDEX idx_memory_agent ON memory_index(agent_id);
CREATE INDEX idx_memory_type ON memory_index(memory_type);
CREATE INDEX idx_memory_tags ON memory_index USING gin(tags);
