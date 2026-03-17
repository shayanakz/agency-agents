-- Migration 001: Configuration Tables
-- These tables define the workflow system's configuration:
-- agents, personas, types, workflows, edges, and overrides.
-- All editable at runtime via Supabase Studio.

-- Types registry (defines all data types flowing between agents)
CREATE TABLE types (
  id text PRIMARY KEY,
  name text NOT NULL,
  description text,
  fields jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Personas (behavioral overlays referencing Agency .md files)
CREATE TABLE personas (
  id text PRIMARY KEY,
  name text NOT NULL,
  source text NOT NULL,
  personality_traits text[] DEFAULT '{}',
  communication_style jsonb DEFAULT '{}',
  domain_expertise text[] DEFAULT '{}',
  decision_biases jsonb DEFAULT '[]',
  prohibited_behaviors text[] DEFAULT '{}',
  system_prompt_overlay text,
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Agents (workflow nodes with typed I/O contracts)
CREATE TABLE agents (
  id text PRIMARY KEY,
  name text NOT NULL,
  description text,
  stage text NOT NULL CHECK (stage IN ('brainstorm', 'planning', 'delivery', 'deployment')),
  role text NOT NULL,
  persona_id text REFERENCES personas(id),
  persona_selector jsonb,
  model_config jsonb NOT NULL,
  inputs jsonb NOT NULL DEFAULT '[]',
  outputs jsonb NOT NULL DEFAULT '[]',
  gates jsonb NOT NULL DEFAULT '[]',
  retry_config jsonb DEFAULT '{"max_attempts": 3, "on_exhausted": "escalate"}',
  guardrails text[] DEFAULT '{}',
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Workflows (pipeline definitions)
CREATE TABLE workflows (
  id text PRIMARY KEY,
  name text NOT NULL,
  version text NOT NULL DEFAULT '1.0.0',
  description text,
  defaults jsonb DEFAULT '{}',
  modes jsonb DEFAULT '{}',
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Workflow phases (ordered stages within a workflow)
CREATE TABLE workflow_phases (
  id text PRIMARY KEY,
  workflow_id text REFERENCES workflows(id) ON DELETE CASCADE,
  name text NOT NULL,
  phase_order int NOT NULL,
  agent_ids text[] NOT NULL,
  sequence text DEFAULT 'serial' CHECK (sequence IN ('serial', 'parallel')),
  approval_required boolean DEFAULT false,
  loop_config jsonb,
  gate_criteria text[] DEFAULT '{}',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE (workflow_id, phase_order)
);

-- Workflow edges (transitions between agents)
CREATE TABLE workflow_edges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id text REFERENCES workflows(id) ON DELETE CASCADE,
  from_agent_id text NOT NULL,
  to_agent_id text NOT NULL,
  condition text NOT NULL,
  carry_forward text[] DEFAULT '{}',
  priority int DEFAULT 0,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Persona overrides (dynamic persona selection per condition)
CREATE TABLE persona_overrides (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id text REFERENCES agents(id) ON DELETE CASCADE,
  persona_id text REFERENCES personas(id),
  condition text NOT NULL,
  priority int DEFAULT 0,
  created_at timestamptz DEFAULT now()
);

-- Updated_at triggers
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_types_updated_at BEFORE UPDATE ON types FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_personas_updated_at BEFORE UPDATE ON personas FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_agents_updated_at BEFORE UPDATE ON agents FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_workflows_updated_at BEFORE UPDATE ON workflows FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_workflow_phases_updated_at BEFORE UPDATE ON workflow_phases FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_workflow_edges_updated_at BEFORE UPDATE ON workflow_edges FOR EACH ROW EXECUTE FUNCTION update_updated_at();
