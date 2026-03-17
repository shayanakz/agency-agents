-- Migration 005: Seed Workflow + Phases + Edges
-- The idea-to-production pipeline definition.

-- Workflow
INSERT INTO workflows (id, name, version, description, defaults, modes) VALUES
('idea-to-production', 'Idea to Production Pipeline', '1.0.0',
  'Complete pipeline from initial idea through brainstorm, planning, development, quality assurance, and production deployment. LangGraph StateGraph is the orchestrator.',
  '{"execution_mode": "supervised", "artifact_dir": "./artifacts", "state_dir": "./state", "checkpoint_backend": "sqlite"}'::jsonb,
  '{"supervised": {"pause_at": ["phase_boundary", "escalation", "deploy"]}, "autonomous": {"pause_at": ["deploy", "escalation"]}}'::jsonb
);

-- Phases (ordered)
INSERT INTO workflow_phases (id, workflow_id, name, phase_order, agent_ids, sequence, approval_required, loop_config, gate_criteria) VALUES

('phase-brainstorm', 'idea-to-production', 'Brainstorm & Research', 1,
  ARRAY['brainstorm_agent'],
  'serial', true, NULL,
  ARRAY['brainstorm_agent.validated_idea.confidence >= 0.6', 'len(brainstorm_agent.research_brief.competitive_landscape) >= 3']
),

('phase-planning', 'idea-to-production', 'Phase Planning & Schema Contract', 2,
  ARRAY['planning_agent', 'schema_verify_agent'],
  'serial', true, NULL,
  ARRAY['schema_verify_agent.schema_valid == true', 'len(planning_agent.sprint_backlog.tasks) >= 1']
),

('phase-delivery', 'idea-to-production', 'Development & Delivery', 3,
  ARRAY['implement_agent', 'code_review_agent', 'qa_agent', 'reality_check_agent'],
  'serial', false,
  '{"over": "planning_agent.sprint_backlog.tasks", "per_task_agents": ["implement_agent", "code_review_agent", "qa_agent", "reality_check_agent"], "per_task_sequence": "serial", "retry": {"trigger": "reality_check_agent.reality_verdict.status == ''NEEDS_WORK''", "restart_at": "implement_agent", "max_attempts": 5, "carry_forward": ["reality_check_agent.reality_verdict"]}, "escalation": {"trigger": "retry.exhausted or reality_check_agent.reality_verdict.status == ''BLOCKED''", "action": "pause_for_human"}}'::jsonb,
  ARRAY['all tasks have reality_check_agent.reality_verdict.status == READY']
),

('phase-deployment', 'idea-to-production', 'Production Deployment', 4,
  ARRAY['deploy_agent'],
  'serial', true, NULL,
  ARRAY['deploy_agent.deployment_manifest.status == SUCCESS', 'deploy_agent.deployment_manifest.health_check_passed == true']
);

-- Edges (agent-to-agent routing within phases)
INSERT INTO workflow_edges (workflow_id, from_agent_id, to_agent_id, condition, carry_forward, priority) VALUES

-- Phase transitions
('idea-to-production', 'brainstorm_agent', 'planning_agent',
  'phase.brainstorm.gate == PASSED', '{}', 10),

('idea-to-production', 'planning_agent', 'schema_verify_agent',
  'always', '{}', 10),

('idea-to-production', 'schema_verify_agent', 'implement_agent',
  'schema_valid == True', '{}', 10),

('idea-to-production', 'schema_verify_agent', 'planning_agent',
  'schema_valid == False', ARRAY['schema_issues'], 5),

-- Delivery phase internal edges (the Dev-QA loop)
('idea-to-production', 'implement_agent', 'code_review_agent',
  'gates.all_passed', '{}', 10),

('idea-to-production', 'code_review_agent', 'qa_agent',
  'review_verdict.status == ''APPROVED''', '{}', 10),

('idea-to-production', 'code_review_agent', 'implement_agent',
  'review_verdict.status == ''CHANGES_REQUESTED''', ARRAY['review_verdict'], 5),

('idea-to-production', 'code_review_agent', '__escalate__',
  'review_verdict.status == ''REJECTED''', ARRAY['review_verdict'], 1),

('idea-to-production', 'qa_agent', 'reality_check_agent',
  'qa_report.overall_verdict == ''PASS''', '{}', 10),

('idea-to-production', 'qa_agent', 'implement_agent',
  'qa_report.overall_verdict in (''FAIL'', ''PARTIAL'')', ARRAY['qa_report'], 5),

-- Reality check routing
('idea-to-production', 'reality_check_agent', 'deploy_agent',
  'reality_verdict.status == ''READY'' and all_tasks_complete', '{}', 10),

('idea-to-production', 'reality_check_agent', 'implement_agent',
  'reality_verdict.status == ''NEEDS_WORK'' and attempt_count < max_attempts', ARRAY['reality_verdict'], 5),

('idea-to-production', 'reality_check_agent', '__escalate__',
  'reality_verdict.status == ''BLOCKED'' or attempt_count >= max_attempts', ARRAY['reality_verdict'], 1),

-- Deploy to complete
('idea-to-production', 'deploy_agent', '__complete__',
  'deployment_manifest.status == ''SUCCESS''', '{}', 10),

('idea-to-production', 'deploy_agent', '__escalate__',
  'deployment_manifest.status != ''SUCCESS''', ARRAY['deployment_manifest'], 1);
