-- Migration 008: Observability — Pipeline Events + Full LLM Audit Trail
--
-- Adds:
-- 1. pipeline_events table for real-time event streaming and audit
-- 2. Expands llm_audit_log with full prompt/response columns
--    (the original only stored prompt_summary — 500 char truncation)

-- ── Pipeline Events table ─────────────────────────────────────
-- Every significant action in the pipeline emits an event.
-- This table is the source of truth for "what happened."
-- It supports: real-time SSE streaming, audit replay, UI dashboards.

CREATE TABLE pipeline_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  agent_id text,
  step_number int,
  data jsonb DEFAULT '{}',
  duration_ms int,
  timestamp timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_pipeline_events_run ON pipeline_events(run_id);
CREATE INDEX idx_pipeline_events_type ON pipeline_events(event_type);
CREATE INDEX idx_pipeline_events_agent ON pipeline_events(agent_id);
CREATE INDEX idx_pipeline_events_timestamp ON pipeline_events(timestamp);
-- Composite index for filtering events by run + type (common UI query)
CREATE INDEX idx_pipeline_events_run_type ON pipeline_events(run_id, event_type);

-- ── Expand llm_audit_log with full conversation columns ──────
-- Previously only stored prompt_summary (first 500 chars).
-- Now stores the complete system prompt, user prompt, and raw response
-- so every LLM call is fully auditable and replayable.

ALTER TABLE llm_audit_log ADD COLUMN IF NOT EXISTS system_prompt text;
ALTER TABLE llm_audit_log ADD COLUMN IF NOT EXISTS user_prompt text;
ALTER TABLE llm_audit_log ADD COLUMN IF NOT EXISTS raw_response text;
ALTER TABLE llm_audit_log ADD COLUMN IF NOT EXISTS parsed_output jsonb;
ALTER TABLE llm_audit_log ADD COLUMN IF NOT EXISTS parse_success boolean DEFAULT true;
ALTER TABLE llm_audit_log ADD COLUMN IF NOT EXISTS session_id text;
