#!/usr/bin/env bash
#
# Validates the workflow system:
#   1. All persona source .md files exist in the repo (ERROR)
#   2. SQL migration files exist and are non-empty (ERROR)
#   3. Python runtime files exist (ERROR)
#   4. Required Python packages are importable (WARN)
#
# Usage: ./scripts/validate-workflow.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKFLOWS_DIR="$REPO_ROOT/workflows"
MIGRATIONS_DIR="$WORKFLOWS_DIR/supabase/migrations"
RUNTIME_DIR="$WORKFLOWS_DIR/runtime"

errors=0
warnings=0

echo "Validating workflow system..."
echo ""

# 1. Check persona source files exist
echo "Checking persona source references..."
PERSONA_SOURCES=(
  "product/product-trend-researcher.md"
  "project-management/project-manager-senior.md"
  "engineering/engineering-backend-architect.md"
  "engineering/engineering-frontend-developer.md"
  "engineering/engineering-senior-developer.md"
  "engineering/engineering-mobile-app-builder.md"
  "testing/testing-evidence-collector.md"
  "testing/testing-reality-checker.md"
  "engineering/engineering-devops-automator.md"
)

for source in "${PERSONA_SOURCES[@]}"; do
  if [[ ! -f "$REPO_ROOT/$source" ]]; then
    echo "ERROR: Persona source file not found: $source"
    errors=$((errors + 1))
  fi
done

# 2. Check migration files exist and are non-empty
echo "Checking migration files..."
EXPECTED_MIGRATIONS=(
  "20260317000001_create_config_tables.sql"
  "20260317000002_create_runtime_tables.sql"
  "20260317000003_seed_personas.sql"
  "20260317000004_seed_agents.sql"
  "20260317000005_seed_workflow.sql"
  "20260317000006_seed_types.sql"
  "20260317000007_create_memory_tables.sql"
)

for migration in "${EXPECTED_MIGRATIONS[@]}"; do
  filepath="$MIGRATIONS_DIR/$migration"
  if [[ ! -f "$filepath" ]]; then
    echo "ERROR: Migration file not found: $migration"
    errors=$((errors + 1))
  elif [[ ! -s "$filepath" ]]; then
    echo "ERROR: Migration file is empty: $migration"
    errors=$((errors + 1))
  fi
done

# 3. Check Python runtime files exist
echo "Checking runtime files..."
EXPECTED_FILES=(
  "__init__.py"
  "db.py"
  "models.py"
  "state.py"
  "persona_loader.py"
  "gate_evaluator.py"
  "llm_router.py"
  "memory.py"
  "graph.py"
  "run.py"
  "config.yaml"
  "requirements.txt"
  "nodes/__init__.py"
  "nodes/base.py"
  "nodes/brainstorm.py"
  "nodes/planning.py"
  "nodes/schema_verify.py"
  "nodes/implement.py"
  "nodes/code_review.py"
  "nodes/qa.py"
  "nodes/reality_check.py"
  "nodes/deploy.py"
)

for file in "${EXPECTED_FILES[@]}"; do
  filepath="$RUNTIME_DIR/$file"
  if [[ ! -f "$filepath" ]]; then
    echo "ERROR: Runtime file not found: runtime/$file"
    errors=$((errors + 1))
  fi
done

# 4. Check Supabase config exists
if [[ ! -f "$WORKFLOWS_DIR/supabase/config.toml" ]]; then
  echo "ERROR: Supabase config not found: supabase/config.toml"
  errors=$((errors + 1))
fi

# 5. Check workflows README
if [[ ! -f "$WORKFLOWS_DIR/README.md" ]]; then
  echo "WARN: Workflows README not found"
  warnings=$((warnings + 1))
fi

# 6. Check Python imports (warning only)
echo "Checking Python imports..."
if command -v python3 &> /dev/null; then
  for pkg in langgraph langchain_anthropic langchain_openai supabase pydantic rich; do
    if ! python3 -c "import $pkg" 2>/dev/null; then
      echo "WARN: Python package not installed: $pkg"
      warnings=$((warnings + 1))
    fi
  done
else
  echo "WARN: python3 not found, skipping import checks"
  warnings=$((warnings + 1))
fi

echo ""
echo "Results: ${errors} error(s), ${warnings} warning(s)."

if [[ $errors -gt 0 ]]; then
  echo "FAILED: fix the errors above."
  exit 1
else
  echo "PASSED"
  exit 0
fi
