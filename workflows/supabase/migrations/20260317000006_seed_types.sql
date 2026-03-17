-- Migration 006: Seed Types
-- Type registry defining all data types flowing between agents.
-- Each type maps to a Pydantic model in the runtime.

INSERT INTO types (id, name, description, fields) VALUES

('RawIdea', 'Raw Idea', 'Initial idea from the user',
  '[{"name": "description", "type": "string", "required": true},
    {"name": "target_audience", "type": "string", "required": false},
    {"name": "constraints", "type": "list[string]", "required": false}]'::jsonb),

('ResearchBrief', 'Research Brief', 'Brainstorm output with validated problem and market research',
  '[{"name": "problem_statement", "type": "string", "required": true},
    {"name": "target_audience", "type": "string", "required": true},
    {"name": "competitive_landscape", "type": "list[Competitor]", "required": true},
    {"name": "feature_list", "type": "FeatureList", "required": true},
    {"name": "technical_recommendations", "type": "string", "required": true},
    {"name": "risks", "type": "list[string]", "required": true},
    {"name": "confidence", "type": "float", "required": true}]'::jsonb),

('ValidatedIdea', 'Validated Idea', 'Idea with confidence score after research',
  '[{"name": "idea", "type": "string", "required": true},
    {"name": "confidence", "type": "float", "required": true},
    {"name": "differentiators", "type": "list[string]", "required": true}]'::jsonb),

('Competitor', 'Competitor', 'Competitor analysis entry',
  '[{"name": "name", "type": "string", "required": true},
    {"name": "strengths", "type": "list[string]", "required": true},
    {"name": "weaknesses", "type": "list[string]", "required": true}]'::jsonb),

('FeatureList', 'Feature List', 'Core and nice-to-have features',
  '[{"name": "core", "type": "list[Feature]", "required": true},
    {"name": "nice_to_have", "type": "list[Feature]", "required": false}]'::jsonb),

('Feature', 'Feature', 'A single feature with optional RICE score',
  '[{"name": "name", "type": "string", "required": true},
    {"name": "description", "type": "string", "required": true},
    {"name": "rice_score", "type": "float", "required": false}]'::jsonb),

('SprintBacklog', 'Sprint Backlog', 'Ordered list of implementation tasks',
  '[{"name": "tasks", "type": "list[Task]", "required": true},
    {"name": "total_estimated_hours", "type": "float", "required": true}]'::jsonb),

('Task', 'Task', 'A single implementation task',
  '[{"name": "id", "type": "string", "required": true},
    {"name": "title", "type": "string", "required": true},
    {"name": "description", "type": "string", "required": true},
    {"name": "category", "type": "enum[frontend,backend,mobile,fullstack,infra]", "required": true},
    {"name": "acceptance_criteria", "type": "list[string]", "required": true},
    {"name": "estimated_hours", "type": "float", "required": true},
    {"name": "dependencies", "type": "list[string]", "required": false}]'::jsonb),

('SchemaContract', 'Schema Contract', 'DB-to-UI field mapping contract',
  '[{"name": "tables", "type": "list[TableMapping]", "required": true},
    {"name": "rpcs", "type": "list[RPCMapping]", "required": false},
    {"name": "mapping_functions", "type": "list[MappingFunction]", "required": true}]'::jsonb),

('TableMapping', 'Table Mapping', 'DB table to UI field mapping',
  '[{"name": "table_name", "type": "string", "required": true},
    {"name": "columns", "type": "list[ColumnMapping]", "required": true}]'::jsonb),

('ColumnMapping', 'Column Mapping', 'Single column mapping',
  '[{"name": "db_column", "type": "string", "required": true},
    {"name": "db_type", "type": "string", "required": true},
    {"name": "ui_field", "type": "string", "required": true},
    {"name": "ui_type", "type": "string", "required": true},
    {"name": "transform", "type": "string", "required": true},
    {"name": "null_handling", "type": "string", "required": true}]'::jsonb),

('RPCMapping', 'RPC Mapping', 'Database RPC function mapping',
  '[{"name": "function_name", "type": "string", "required": true},
    {"name": "parameters", "type": "list[ColumnMapping]", "required": true},
    {"name": "returns", "type": "list[ColumnMapping]", "required": true}]'::jsonb),

('MappingFunction', 'Mapping Function', 'Code mapping function definition',
  '[{"name": "name", "type": "string", "required": true},
    {"name": "from_type", "type": "string", "required": true},
    {"name": "to_type", "type": "string", "required": true},
    {"name": "used_by", "type": "list[string]", "required": true}]'::jsonb),

('ArchitectureDoc', 'Architecture Document', 'Technical architecture decisions',
  '[{"name": "tech_stack", "type": "dict", "required": true},
    {"name": "data_model", "type": "string", "required": true},
    {"name": "api_contracts", "type": "string", "required": true},
    {"name": "deployment_target", "type": "enum[vercel,netlify,docker,expo,static]", "required": true}]'::jsonb),

('CodeArtifact', 'Code Artifact', 'Implementation output with files and test results',
  '[{"name": "files_changed", "type": "list[FileChange]", "required": true},
    {"name": "test_results", "type": "string", "required": false},
    {"name": "schema_compliant", "type": "boolean", "required": true}]'::jsonb),

('FileChange', 'File Change', 'A single file creation or modification',
  '[{"name": "path", "type": "string", "required": true},
    {"name": "content", "type": "string", "required": true},
    {"name": "action", "type": "enum[create,modify,delete]", "required": true}]'::jsonb),

('ReviewVerdict', 'Review Verdict', 'Code review outcome',
  '[{"name": "status", "type": "enum[APPROVED,CHANGES_REQUESTED,REJECTED]", "required": true},
    {"name": "comments", "type": "list[ReviewComment]", "required": true},
    {"name": "blocking_issues", "type": "list[string]", "required": true}]'::jsonb),

('ReviewComment', 'Review Comment', 'A single code review comment',
  '[{"name": "file", "type": "string", "required": true},
    {"name": "line", "type": "integer", "required": false},
    {"name": "severity", "type": "enum[critical,high,medium,low,suggestion]", "required": true},
    {"name": "comment", "type": "string", "required": true}]'::jsonb),

('QAReport', 'QA Report', 'Structured QA findings with evidence',
  '[{"name": "overall_verdict", "type": "enum[PASS,FAIL,PARTIAL]", "required": true},
    {"name": "screenshots", "type": "list[Screenshot]", "required": true},
    {"name": "issues", "type": "list[Issue]", "required": true},
    {"name": "criteria_checked", "type": "list[CriterionResult]", "required": true},
    {"name": "console_errors", "type": "list[string]", "required": true},
    {"name": "accessibility_findings", "type": "list[string]", "required": false}]'::jsonb),

('Screenshot', 'Screenshot', 'A captured screenshot with metadata',
  '[{"name": "name", "type": "string", "required": true},
    {"name": "path", "type": "string", "required": true},
    {"name": "viewport", "type": "enum[desktop,tablet,mobile]", "required": true},
    {"name": "theme", "type": "enum[light,dark]", "required": true}]'::jsonb),

('CriterionResult', 'Criterion Result', 'Result of checking one acceptance criterion',
  '[{"name": "criterion", "type": "string", "required": true},
    {"name": "status", "type": "enum[PASS,FAIL,SKIP]", "required": true},
    {"name": "evidence", "type": "string", "required": false}]'::jsonb),

('Issue', 'Issue', 'A found issue with severity and evidence',
  '[{"name": "id", "type": "string", "required": true},
    {"name": "severity", "type": "enum[critical,high,medium,low]", "required": true},
    {"name": "description", "type": "string", "required": true},
    {"name": "evidence", "type": "string", "required": false},
    {"name": "file", "type": "string", "required": false},
    {"name": "line", "type": "integer", "required": false}]'::jsonb),

('RealityCheckVerdict', 'Reality Check Verdict', 'Final quality assessment',
  '[{"name": "status", "type": "enum[READY,NEEDS_WORK,BLOCKED]", "required": true},
    {"name": "quality_rating", "type": "string", "required": true},
    {"name": "issues", "type": "list[Issue]", "required": true},
    {"name": "evidence_references", "type": "list[string]", "required": true},
    {"name": "spec_compliance", "type": "list[CriterionResult]", "required": true},
    {"name": "fix_instructions", "type": "list[FixInstruction]", "required": false}]'::jsonb),

('FixInstruction', 'Fix Instruction', 'Specific instruction to fix an issue',
  '[{"name": "issue_id", "type": "string", "required": true},
    {"name": "instruction", "type": "string", "required": true},
    {"name": "files_to_modify", "type": "list[string]", "required": true}]'::jsonb),

('DeploymentManifest', 'Deployment Manifest', 'Deployment outcome with rollback info',
  '[{"name": "status", "type": "enum[SUCCESS,FAILED,ROLLED_BACK]", "required": true},
    {"name": "url", "type": "string", "required": false},
    {"name": "health_check_passed", "type": "boolean", "required": true},
    {"name": "rollback_instructions", "type": "string", "required": true},
    {"name": "deployment_log", "type": "string", "required": true}]'::jsonb);
