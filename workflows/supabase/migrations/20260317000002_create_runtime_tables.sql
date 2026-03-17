-- Migration 002: Runtime Tables
-- These tables track pipeline execution state:
-- runs, steps, handoffs, approvals, artifacts, and LLM audit logs.

-- Pipeline runs (one per CLI invocation)
CREATE TABLE pipeline_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id text REFERENCES workflows(id),
  project_name text NOT NULL,
  ideas text[] NOT NULL,
  mode text NOT NULL DEFAULT 'supervised' CHECK (mode IN ('supervised', 'autonomous')),
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'paused', 'complete', 'failed', 'blocked')),
  current_stage text,
  current_task_index int DEFAULT 0,
  total_tasks int DEFAULT 0,
  state_snapshot jsonb DEFAULT '{}',
  error text,
  started_at timestamptz DEFAULT now(),
  completed_at timestamptz,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Run steps (one per agent execution within a run)
CREATE TABLE run_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  agent_id text REFERENCES agents(id),
  persona_id text REFERENCES personas(id),
  step_number int NOT NULL,
  attempt int DEFAULT 1,
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'passed', 'failed', 'skipped', 'waiting')),
  inputs jsonb,
  outputs jsonb,
  gate_results jsonb,
  error text,
  duration_ms int,
  started_at timestamptz DEFAULT now(),
  completed_at timestamptz,
  created_at timestamptz DEFAULT now()
);

-- Handoffs (every agent-to-agent transition)
CREATE TABLE handoffs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  step_id uuid REFERENCES run_steps(id),
  from_agent_id text NOT NULL,
  to_agent_id text NOT NULL,
  payload jsonb,
  gate_results jsonb,
  carry_forward jsonb,
  created_at timestamptz DEFAULT now()
);

-- Approvals (human approval checkpoints)
CREATE TABLE approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  step_id uuid REFERENCES run_steps(id),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'modified')),
  decided_by text,
  decided_at timestamptz,
  modification jsonb,
  notes text,
  requested_at timestamptz DEFAULT now()
);

-- Artifacts (files produced by agents)
CREATE TABLE artifacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  step_id uuid REFERENCES run_steps(id),
  artifact_type text NOT NULL CHECK (artifact_type IN ('doc', 'screenshot', 'code', 'test_result')),
  name text NOT NULL,
  file_path text,
  content text,
  mime_type text,
  created_at timestamptz DEFAULT now()
);

-- LLM audit log (every model call)
CREATE TABLE llm_audit_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  step_id uuid REFERENCES run_steps(id) ON DELETE SET NULL,
  agent_id text,
  execution_type text NOT NULL CHECK (execution_type IN ('llm_api', 'claude_code', 'claude_code_print', 'codex')),
  provider text,
  model text NOT NULL,
  temperature float,
  prompt_summary text,
  input_tokens int,
  output_tokens int,
  cost_usd decimal(10,6),
  latency_ms int,
  created_at timestamptz DEFAULT now()
);

-- Indexes for query performance
CREATE INDEX idx_pipeline_runs_status ON pipeline_runs(status);
CREATE INDEX idx_pipeline_runs_workflow ON pipeline_runs(workflow_id);
CREATE INDEX idx_run_steps_run_id ON run_steps(run_id);
CREATE INDEX idx_run_steps_status ON run_steps(status);
CREATE INDEX idx_run_steps_agent ON run_steps(agent_id);
CREATE INDEX idx_handoffs_run_id ON handoffs(run_id);
CREATE INDEX idx_artifacts_run_id ON artifacts(run_id);
CREATE INDEX idx_artifacts_step ON artifacts(step_id);
CREATE INDEX idx_llm_audit_run_id ON llm_audit_log(run_id);
CREATE INDEX idx_llm_audit_agent ON llm_audit_log(agent_id);
CREATE INDEX idx_approvals_status ON approvals(status);
CREATE INDEX idx_approvals_run ON approvals(run_id);

-- Updated_at trigger for pipeline_runs
CREATE TRIGGER trg_pipeline_runs_updated_at BEFORE UPDATE ON pipeline_runs FOR EACH ROW EXECUTE FUNCTION update_updated_at();
