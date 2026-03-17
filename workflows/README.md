# Workflows: Multi-Agent Pipeline System

A strict, database-backed multi-agent workflow system that transforms ideas into production-ready applications. Each pipeline step is a dedicated agent with typed I/O contracts, quality gates, and retry logic — orchestrated by LangGraph.

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │     LangGraph StateGraph             │
                    │     (THE ORCHESTRATOR)                │
                    │                                      │
                    │  Reads config from: Supabase          │
                    │  Persists state via: SqliteSaver      │
                    │  Enforces: gates, retries, sequence   │
                    └──────────────┬───────────────────────┘
                                   │
  brainstorm → planning → schema_verify → [implement → review → QA → reality_check] → deploy
                                            └─────── retry loop (max 5) ──────────┘
```

**Key design decisions:**

- **The graph IS the orchestrator.** No "orchestrator agent." LangGraph evaluates gates, routes edges, and manages retries deterministically.
- **Two execution modes:** `llm_api` (thinking agents call APIs) and `claude_code` (action agents spawn Claude Code with file/shell/MCP tools).
- **Agent vs Persona separation:** Agents define contracts (I/O, gates, retries). Personas define behavior (personality, biases). Swappable at runtime.
- **All config in Supabase.** Edit agents, personas, gates, edges via Supabase Studio. No redeploy needed.

## Quick Start

### Prerequisites

- Python 3.11+
- [Supabase CLI](https://supabase.com/docs/guides/cli)
- Docker (for Supabase local)
- API keys: `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`

### Setup

```bash
# 1. Start local Supabase
cd workflows/supabase
supabase start

# 2. Apply migrations (creates tables + seeds data)
supabase db push

# 3. Install Python dependencies
cd ../runtime
pip install -r requirements.txt

# 4. Set environment variables
export SUPABASE_URL="http://localhost:54321"
export SUPABASE_SERVICE_KEY="<your-local-service-key>"  # from supabase start output
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."  # optional, for code review agent
```

### Run a Pipeline

```bash
# Supervised mode (pauses for approval at each phase)
python -m runtime.run --project "MyApp" --idea "a nutrition tracking app with barcode scanning" --stream

# Autonomous mode (runs end-to-end, pauses only before deploy)
python -m runtime.run --project "MyApp" --idea "a nutrition tracking app" --autonomous

# Multiple ideas
python -m runtime.run --project "Suite" --idea "feature A" --idea "feature B" --stream
```

### Configure via Supabase Studio

Open `http://localhost:54323` to:

- **Edit agents:** Change models, temperatures, gates, guardrails
- **Swap personas:** Attach different Agency personalities to any agent
- **Tune gates:** Adjust threshold predicates (e.g., lower confidence from 0.6 to 0.5)
- **View runs:** Browse pipeline_runs, run_steps, handoffs, llm_audit_log
- **Monitor costs:** Check llm_audit_log for token usage and latency

## Directory Structure

```
workflows/
  supabase/
    migrations/
      001_create_config_tables.sql    # agents, personas, types, workflows, edges
      002_create_runtime_tables.sql   # pipeline_runs, run_steps, handoffs, audit
      003_seed_agents.sql             # 8 agent definitions
      004_seed_personas.sql           # 9 persona bindings
      005_seed_workflow.sql           # idea-to-production graph + edges
      006_seed_types.sql              # type registry
    config.toml
  runtime/
    db.py                             # Supabase client + queries
    models.py                         # Pydantic models for all types
    state.py                          # PipelineState TypedDict
    persona_loader.py                 # System prompt builder (5 layers)
    gate_evaluator.py                 # Deterministic predicate evaluator
    llm_router.py                     # Routes to Anthropic/OpenAI/Claude Code/Codex
    graph.py                          # LangGraph StateGraph construction
    run.py                            # CLI entrypoint
    nodes/
      base.py                         # Base node pattern
      brainstorm.py                   # Stage 1: Research & validation
      planning.py                     # Stage 2: Sprint backlog + schema contract
      schema_verify.py                # Stage 2: Schema verification
      implement.py                    # Stage 3: Code generation (Claude Code)
      code_review.py                  # Stage 3: Code review (different LLM)
      qa.py                           # Stage 3: QA with screenshots (Claude Code)
      reality_check.py                # Stage 3: Final quality gate
      deploy.py                       # Stage 4: Production deployment
```

## Agents

| Agent | Stage | Execution | Default Model | Persona |
|-------|-------|-----------|---------------|---------|
| brainstorm_agent | brainstorm | llm_api | Claude Opus (0.7) | Trend Researcher |
| planning_agent | planning | llm_api | Claude Opus (0.3) | Senior PM |
| schema_verify_agent | planning | llm_api | Claude Sonnet (0.0) | Backend Architect |
| implement_agent | delivery | claude_code | Claude Sonnet | Dynamic (per task category) |
| code_review_agent | delivery | llm_api | GPT-4.5 (0.2) | Senior Developer |
| qa_agent | delivery | claude_code | Claude Sonnet | Evidence Collector |
| reality_check_agent | delivery | llm_api | Claude Opus (0.2) | Reality Checker |
| deploy_agent | deployment | claude_code | Claude Sonnet | DevOps Automator |

## Adding New Agents

1. Insert a row in the `agents` table (via Studio or SQL)
2. Create a persona binding in the `personas` table
3. Add a node function in `runtime/nodes/`
4. Register it in `runtime/nodes/__init__.py` and `runtime/graph.py`
5. Add edges in `workflow_edges` table

## Adding New Personas

1. The Agency .md file already exists in the repo (e.g., `engineering/engineering-security-engineer.md`)
2. Insert a row in the `personas` table with `source` pointing to the .md file
3. Extract personality_traits, decision_biases, prohibited_behaviors from the .md
4. Attach to any agent by updating its `persona_id`

## Production Deployment

```bash
# Link to hosted Supabase project
cd workflows/supabase
supabase link --project-ref <your-project-id>

# Push migrations to production
supabase db push

# Update environment variables
export SUPABASE_URL="https://<your-project>.supabase.co"
export SUPABASE_SERVICE_KEY="<production-service-key>"
```

Same code, same schema, different environment variables.
