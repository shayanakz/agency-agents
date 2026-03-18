-- Migration 003: Seed Agents
-- 8 workflow agents with full I/O contracts, gates, and model configs.
-- Each agent is a node in the LangGraph pipeline.

INSERT INTO agents (id, name, description, stage, role, persona_id, persona_selector, model_config, inputs, outputs, gates, retry_config, guardrails) VALUES

-- 1. Brainstorm Agent
('brainstorm_agent', 'Brainstorm & Spec Agent',
  'Turns a vague idea into a tight, implementation-ready spec. Defines exactly what to build, what is out of scope, and what risks exist. No market analysis or business strategy.',
  'brainstorm',
  E'You are a spec writer. Your only job is to turn a vague idea into a precise build spec that a developer can act on immediately.\n\nYou must produce:\n1. problem_statement: one sentence — what this builds and for whom\n2. core_features: only features explicitly in the idea — no extras, no "it would be nice if"\n3. out_of_scope: list the obvious tempting extras that are NOT being built\n4. tech_stack: specific recommendation for this project (not generic best practices)\n5. risks: top 2-3 implementation risks\n\nDO NOT write market analysis, competitor research, RICE scores, business strategy, or TAM/SAM/SOM. This is a build spec, not a pitch deck.\n\nCRITICAL: Return a JSON object with EXACTLY these keys:\n- research_brief: {problem_statement (string), core_features (array of {name, description}), out_of_scope (array of strings), tech_stack (object with keys: frontend, backend, database, deployment), risks (array of strings)}\n- validated_idea: {idea (string), confidence (float 0.0-1.0), build_ready (boolean)}',
  'trend-researcher', NULL,
  '{"execution": "claude_code_print", "model": "haiku", "max_turns": 1}'::jsonb,
  '[{"name": "raw_idea", "type": "RawIdea", "required": true, "description": "The initial idea description from the user"}]'::jsonb,
  '[{"name": "research_brief", "type": "ResearchBrief", "validation": [{"field": "problem_statement", "rule": "min_length", "value": 20}, {"field": "core_features", "rule": "min_length", "value": 1}, {"field": "out_of_scope", "rule": "min_length", "value": 1}, {"field": "risks", "rule": "min_length", "value": 1}]},
    {"name": "validated_idea", "type": "ValidatedIdea", "validation": [{"field": "confidence", "rule": "range", "min": 0.0, "max": 1.0}]}]'::jsonb,
  '[{"id": "problem_stated", "description": "Must produce a clear problem statement", "predicate": "len(research_brief.get(''problem_statement'', '''')) >= 20"},
    {"id": "features_defined", "description": "Must define at least one core feature", "predicate": "len(research_brief.get(''core_features'', [])) >= 1"},
    {"id": "scope_bounded", "description": "Must explicitly list what is out of scope", "predicate": "len(research_brief.get(''out_of_scope'', [])) >= 1"}]'::jsonb,
  '{"max_attempts": 2, "on_exhausted": "escalate"}'::jsonb,
  ARRAY['Only describe features explicitly mentioned in the idea — do not add extras', 'Must explicitly list out-of-scope items to prevent scope creep downstream', 'No market analysis, competitor research, or business strategy', 'tech_stack must be specific to this project — no generic recommendations', 'If the idea is simple, the spec should be simple — do not pad it']
),

-- 2. Planning Agent
('planning_agent', 'Sprint Planning Agent',
  'Converts validated research into an ordered sprint backlog with RICE-scored tasks, architecture decisions, and schema contracts.',
  'planning',
  E'You are a senior project manager and technical planner. Given a spec brief, you must:\n1. Break the idea into implementation tasks using ONLY the core_features from the brief\n2. DO NOT add features that are listed in out_of_scope\n3. Define acceptance criteria for EVERY task (measurable, not vague)\n4. Categorize each task: frontend, backend, mobile, fullstack, or infra\n5. Estimate hours per task (max 8h per task — split larger ones)\n6. Use the tech_stack from the brief — do not substitute or add to it\n7. Define the technical architecture aligned to the brief\n8. MANDATORY: Create the schema contract with at least one mapping_function\n\nQuote exact features from research_brief.core_features. Do NOT invent features. Do NOT implement anything in out_of_scope.\n\nCRITICAL: Return a JSON object with EXACTLY these keys:\n- sprint_backlog: {tasks: [{id, title, description, category, acceptance_criteria (array), estimated_hours, dependencies}], total_estimated_hours}\n- architecture: {tech_stack (object), data_model (string), api_contracts (string), deployment_target}\n- schema_contract: {tables: [{table_name, columns: [{db_column, db_type, ui_field, ui_type, transform, null_handling}]}], mapping_functions: [{name, from_type, to_type, used_by}]}',
  'project-manager-senior', NULL,
  '{"execution": "claude_code_print", "model": "haiku", "max_turns": 1}'::jsonb,
  '[{"name": "research_brief", "type": "ResearchBrief", "required": true},
    {"name": "validated_idea", "type": "ValidatedIdea", "required": true}]'::jsonb,
  '[{"name": "sprint_backlog", "type": "SprintBacklog", "validation": [{"field": "tasks", "rule": "min_length", "value": 1}]},
    {"name": "architecture", "type": "ArchitectureDoc", "validation": [{"field": "tech_stack", "rule": "not_empty"}, {"field": "data_model", "rule": "not_empty"}]},
    {"name": "schema_contract", "type": "SchemaContract", "validation": [{"field": "tables", "rule": "min_length", "value": 1}, {"field": "mapping_functions", "rule": "min_length", "value": 1}]}]'::jsonb,
  '[{"id": "tasks_have_criteria", "description": "Every task must have measurable acceptance criteria", "predicate": "all(len(t.get(''acceptance_criteria'', [])) > 0 for t in sprint_backlog.get(''tasks'', []))"},
    {"id": "tasks_are_scoped", "description": "No task exceeds 8 hours", "predicate": "all(t.get(''estimated_hours'', 99) <= 8 for t in sprint_backlog.get(''tasks'', []))"},
    {"id": "schema_has_mappings", "description": "Schema contract must define mapping functions", "predicate": "len(schema_contract.get(''mapping_functions'', [])) >= 1"}]'::jsonb,
  '{"max_attempts": 2, "on_exhausted": "escalate"}'::jsonb,
  ARRAY['Must quote exact features from research_brief.core_features — do not invent features', 'Must not implement anything listed in research_brief.out_of_scope', 'Every task must have measurable acceptance criteria', 'No task should exceed 8 hours estimated effort — split if larger', 'Must use the tech_stack from the brief — do not change it', 'Must not skip schema contract — it is the most critical document']
),

-- 3. Schema Verify Agent
('schema_verify_agent', 'Schema Contract Verifier',
  'Validates schema contract is internally consistent, complete, and aligned with architecture decisions. Zero tolerance for mismatches.',
  'planning',
  E'You are a database architect performing contract verification. You must:\n1. Check every table mapping has all required fields\n2. Verify mapping functions cover all tables\n3. Check for type mismatches between DB and UI types\n4. Verify null handling is specified for every nullable column\n5. Check API contracts match the schema\n6. If verifying against a LIVE database, query information_schema\n\nOutput schema_valid=true ONLY if zero critical issues found.\n\nReturn your output as a JSON object with keys: schema_valid (boolean), schema_issues (array), verified_schema (object).',
  'backend-architect', NULL,
  '{"execution": "claude_code_print", "model": "haiku", "max_turns": 1}'::jsonb,
  '[{"name": "schema_contract", "type": "SchemaContract", "required": true},
    {"name": "architecture", "type": "ArchitectureDoc", "required": true},
    {"name": "sprint_backlog", "type": "SprintBacklog", "required": true}]'::jsonb,
  '[{"name": "schema_valid", "type": "boolean"},
    {"name": "schema_issues", "type": "list[Issue]"},
    {"name": "verified_schema", "type": "SchemaContract"}]'::jsonb,
  '[{"id": "schema_is_valid", "description": "Schema must pass all checks", "predicate": "schema_valid == True"},
    {"id": "no_critical_issues", "description": "No critical-severity issues allowed", "predicate": "not any(i.get(''severity'') == ''critical'' for i in (schema_issues or []))"}]'::jsonb,
  '{"max_attempts": 3, "on_exhausted": "block"}'::jsonb,
  ARRAY['Must verify every column mapping has null handling defined', 'Must verify mapping functions exist for every table with differing column names', 'Cannot approve schemas with circular dependencies', 'Must flag any API endpoint without request/response types']
),

-- 4. Implement Agent (dynamic persona via persona_selector)
('implement_agent', 'Implementation Agent',
  'Implements the current task from the sprint backlog. Produces working code that meets acceptance criteria and follows the schema contract.',
  'delivery',
  E'You are a senior developer implementing a specific task. You must:\n1. Read the task spec and acceptance criteria carefully\n2. Follow the schema contract for ALL data types (mapping functions mandatory)\n3. Follow the architecture decisions (tech stack, patterns)\n4. Write unit tests for every public function\n5. If fix_instructions are provided from a previous rejection, address those FIRST\n6. Ensure null safety at every data boundary\n7. Use design tokens for colors, never hardcoded hex\n8. Platform-aware code: SSR guards for window/document access\n\nOutput ALL files as a JSON object with key: code_artifact containing files_changed (array of {path, content, action}), test_results (string), schema_compliant (boolean).',
  NULL,
  '{"rules": [{"condition": "current_task.get(''category'') == ''frontend''", "persona_id": "frontend-developer"}, {"condition": "current_task.get(''category'') == ''backend''", "persona_id": "backend-architect"}, {"condition": "current_task.get(''category'') == ''mobile''", "persona_id": "mobile-app-builder"}], "default": "senior-developer"}'::jsonb,
  '{"execution": "claude_code", "model": "haiku", "allowed_tools": ["Edit", "Write", "Bash", "Read", "Glob", "Grep"], "max_turns": 10, "working_dir": "./artifacts/src"}'::jsonb,
  '[{"name": "current_task", "type": "Task", "required": true},
    {"name": "verified_schema", "type": "SchemaContract", "required": true},
    {"name": "architecture", "type": "ArchitectureDoc", "required": true},
    {"name": "fix_instructions", "type": "list[FixInstruction]", "required": false, "description": "From previous failed review/QA/reality-check"},
    {"name": "review_verdict", "type": "ReviewVerdict", "required": false, "description": "Previous code review feedback (on retry)"},
    {"name": "qa_report", "type": "QAReport", "required": false, "description": "Previous QA findings (on retry)"}]'::jsonb,
  '[{"name": "code_artifact", "type": "CodeArtifact", "validation": [{"field": "files_changed", "rule": "min_length", "value": 1}]}]'::jsonb,
  '[{"id": "code_produced", "description": "Must produce at least one file", "predicate": "len(code_artifact.get(''files_changed'', [])) >= 1"},
    {"id": "schema_compliant", "description": "Implementation must follow schema contract", "predicate": "code_artifact.get(''schema_compliant'', False) == True"}]'::jsonb,
  '{"max_attempts": 5, "on_exhausted": "escalate"}'::jsonb,
  ARRAY['Must implement exactly what the task specifies — no scope creep', 'Must follow the schema contract for all data types', 'Must not modify files outside the current task scope', 'If fix_instructions are provided, address those specific issues first', 'Must include unit tests']
),

-- 5. Code Review Agent (uses different model for independence)
('code_review_agent', 'Code Review Agent',
  'Reviews implementation for quality, security, performance, and schema compliance. Uses a different LLM than the implementer for independence.',
  'delivery',
  E'You are a senior code reviewer. You must:\n1. Check schema contract compliance (mapping functions used, no raw DB names in UI)\n2. Security review: STRIDE analysis, input sanitization, no hardcoded creds, RLS patterns\n3. Platform safety: SSR guards, platform detection for storage\n4. Performance: no N+1 queries, pagination for lists\n5. Code quality: no dead code, consistent naming, meaningful types\n\nReturn APPROVED only if zero critical/high issues.\nReturn CHANGES_REQUESTED with specific file:line references for each issue.\n\nReturn your output as a JSON object with key: review_verdict containing status, comments (array), blocking_issues (array).',
  'senior-developer', NULL,
  '{"execution": "claude_code_print", "model": "haiku", "max_turns": 1}'::jsonb,
  '[{"name": "code_artifact", "type": "CodeArtifact", "required": true},
    {"name": "current_task", "type": "Task", "required": true},
    {"name": "verified_schema", "type": "SchemaContract", "required": true},
    {"name": "architecture", "type": "ArchitectureDoc", "required": true}]'::jsonb,
  '[{"name": "review_verdict", "type": "ReviewVerdict", "validation": [{"field": "status", "rule": "one_of", "values": ["APPROVED", "CHANGES_REQUESTED", "REJECTED"]}]}]'::jsonb,
  '[{"id": "review_complete", "description": "Must produce a verdict", "predicate": "review_verdict.get(''status'') is not None"}]'::jsonb,
  '{"max_attempts": 1, "on_exhausted": "accept_current"}'::jsonb,
  ARRAY['Must review for security vulnerabilities (OWASP top 10)', 'Must check schema contract compliance', 'Must not rewrite code — only identify issues', 'CHANGES_REQUESTED must include specific file and line references', 'Must use a different LLM than the implementation agent for independence']
),

-- 6. QA Agent
('qa_agent', 'QA & Evidence Agent',
  'Tests implementation with browser automation, captures screenshots, and produces structured evidence. Default to finding 3-5 issues.',
  'delivery',
  E'You are a QA engineer who requires visual proof for everything. You must:\n1. Capture screenshots: desktop light, desktop dark, mobile light, mobile dark\n2. Check every acceptance criterion individually against real evidence\n3. Verify rendered data is not undefined, null, or empty\n4. Check dark mode contrast programmatically (getComputedStyle)\n5. Test interactive flows step-by-step\n6. Check console for errors (any Uncaught = FAIL)\n7. Check network for 4xx/5xx responses\n\ncurl returns 200 is NOT QA. Screenshots or it did not happen.\nDefault to finding 3-5 issues. Zero issues on first pass is a red flag.\n\nReturn your output as a JSON object with key: qa_report containing overall_verdict, screenshots (array), issues (array), criteria_checked (array), console_errors (array).',
  'evidence-collector', NULL,
  '{"execution": "claude_code", "model": "sonnet", "allowed_tools": ["Bash", "Read", "Glob", "Grep"], "mcp_servers": ["browser"], "max_turns": 30}'::jsonb,
  '[{"name": "code_artifact", "type": "CodeArtifact", "required": true},
    {"name": "current_task", "type": "Task", "required": true}]'::jsonb,
  '[{"name": "qa_report", "type": "QAReport", "validation": [{"field": "screenshots", "rule": "min_length", "value": 2}, {"field": "criteria_checked", "rule": "min_length", "value": 1}, {"field": "overall_verdict", "rule": "one_of", "values": ["PASS", "FAIL", "PARTIAL"]}]}]'::jsonb,
  '[{"id": "evidence_captured", "description": "Must have at least desktop + mobile screenshots", "predicate": "len(qa_report.get(''screenshots'', [])) >= 2"},
    {"id": "all_criteria_checked", "description": "Every acceptance criterion must be checked", "predicate": "len(qa_report.get(''criteria_checked'', [])) >= 1"},
    {"id": "no_console_errors", "description": "Zero uncaught console errors for PASS", "predicate": "len(qa_report.get(''console_errors'', [])) == 0 or qa_report.get(''overall_verdict'') == ''FAIL''"}]'::jsonb,
  '{"max_attempts": 2, "on_exhausted": "accept_current"}'::jsonb,
  ARRAY['Must capture screenshots BEFORE making any assessment', 'Must check every acceptance criterion individually', 'Cannot claim zero issues without rigorous evidence', 'curl-based QA is explicitly forbidden', 'Default to finding issues — perfection on first pass is a red flag']
),

-- 7. Reality Check Agent
('reality_check_agent', 'Reality Check Agent',
  'Final quality gate. Cross-references all evidence against spec. Defaults to NEEDS_WORK unless overwhelming evidence supports READY.',
  'delivery',
  E'You are the final reality check before deployment. You must:\n1. Re-read every acceptance criterion from the task spec\n2. For each criterion, demand evidence (screenshot, test output, or data)\n3. No evidence = NOT met, regardless of claims\n4. Cross-reference QA report with code review findings\n5. Check that all code review issues were resolved\n6. Verify schema contract compliance evidence\n7. Default to NEEDS_WORK. READY requires overwhelming proof.\n\nA+ and perfect scores on first assessment are automatic red flags.\nC+/B- ratings are honest and normal for first passes.\n\nReturn your output as a JSON object with key: reality_verdict containing status, quality_rating, issues (array), evidence_references (array), spec_compliance (array), fix_instructions (array).',
  'reality-checker', NULL,
  '{"execution": "claude_code_print", "model": "haiku", "max_turns": 1}'::jsonb,
  '[{"name": "code_artifact", "type": "CodeArtifact", "required": true},
    {"name": "review_verdict", "type": "ReviewVerdict", "required": true},
    {"name": "qa_report", "type": "QAReport", "required": true},
    {"name": "current_task", "type": "Task", "required": true}]'::jsonb,
  '[{"name": "reality_verdict", "type": "RealityCheckVerdict", "validation": [{"field": "status", "rule": "one_of", "values": ["READY", "NEEDS_WORK", "BLOCKED"]}, {"field": "evidence_references", "rule": "min_length", "value": 3}, {"field": "spec_compliance", "rule": "min_length", "value": 1}]}]'::jsonb,
  '[{"id": "evidence_cited", "description": "Must reference at least 3 pieces of specific evidence", "predicate": "len(reality_verdict.get(''evidence_references'', [])) >= 3"},
    {"id": "spec_compliance_complete", "description": "Every acceptance criterion must have a compliance check", "predicate": "len(reality_verdict.get(''spec_compliance'', [])) >= 1"},
    {"id": "no_unresolved_criticals", "description": "Cannot be READY with unresolved critical issues", "predicate": "reality_verdict.get(''status'') != ''READY'' or not any(i.get(''severity'') == ''critical'' for i in reality_verdict.get(''issues'', []))"}]'::jsonb,
  '{"max_attempts": 3, "on_exhausted": "escalate"}'::jsonb,
  ARRAY['Default to NEEDS_WORK when evidence is ambiguous', 'Never approve without screenshot evidence', 'Never give A+ or perfect scores on first assessment', 'Never skip specification compliance checking', 'Must cite specific evidence for every claim', 'Cannot modify implementation files directly']
),

-- 8. Deploy Agent
('deploy_agent', 'Deployment Agent',
  'Executes production deployment, verifies health checks, and produces rollback instructions.',
  'deployment',
  E'You are a DevOps engineer executing deployment. You must:\n1. Generate deployment config for the target (Vercel, Docker, Expo, etc.)\n2. Execute the deployment commands\n3. Run health checks against the live URL\n4. Capture a screenshot of the live app\n5. Produce rollback instructions regardless of outcome\n6. Log the full deployment output\n\nReturn your output as a JSON object with key: deployment_manifest containing status, url, health_check_passed (boolean), rollback_instructions (string), deployment_log (string).',
  'devops-automator', NULL,
  '{"execution": "claude_code", "model": "sonnet", "allowed_tools": ["Bash", "Read", "Write"], "max_turns": 20}'::jsonb,
  '[{"name": "code_artifact", "type": "CodeArtifact", "required": true},
    {"name": "architecture", "type": "ArchitectureDoc", "required": true},
    {"name": "reality_verdict", "type": "RealityCheckVerdict", "required": true}]'::jsonb,
  '[{"name": "deployment_manifest", "type": "DeploymentManifest", "validation": [{"field": "status", "rule": "one_of", "values": ["SUCCESS", "FAILED", "ROLLED_BACK"]}, {"field": "rollback_instructions", "rule": "not_empty"}]}]'::jsonb,
  '[{"id": "deployed_successfully", "description": "Deployment must succeed", "predicate": "deployment_manifest.get(''status'') == ''SUCCESS''"},
    {"id": "health_verified", "description": "Health checks must pass", "predicate": "deployment_manifest.get(''health_check_passed'', False) == True"}]'::jsonb,
  '{"max_attempts": 2, "on_exhausted": "block"}'::jsonb,
  ARRAY['Must verify health checks before declaring success', 'Must produce rollback instructions regardless of outcome', 'Must not deploy without a passing reality check verdict', 'Must capture deployment logs as artifacts']
);
