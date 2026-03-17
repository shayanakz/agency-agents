"""Pydantic models for all workflow data types.

These correspond to the type definitions stored in the Supabase `types` table.
They are used for validation when parsing LLM outputs.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ───────────────────────────────────────────────────────


class TaskCategory(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    MOBILE = "mobile"
    FULLSTACK = "fullstack"
    INFRA = "infra"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewStatus(str, Enum):
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"


class QAVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"


class RealityStatus(str, Enum):
    READY = "READY"
    NEEDS_WORK = "NEEDS_WORK"
    BLOCKED = "BLOCKED"


class DeployStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class FileAction(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class Viewport(str, Enum):
    DESKTOP = "desktop"
    TABLET = "tablet"
    MOBILE = "mobile"


class Theme(str, Enum):
    LIGHT = "light"
    DARK = "dark"


class CriterionStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


class DeployTarget(str, Enum):
    VERCEL = "vercel"
    NETLIFY = "netlify"
    DOCKER = "docker"
    EXPO = "expo"
    STATIC = "static"


# ── Models ──────────────────────────────────────────────────────


class RawIdea(BaseModel):
    description: str
    target_audience: Optional[str] = None
    constraints: list[str] = Field(default_factory=list)


class Competitor(BaseModel):
    name: str
    strengths: list[str]
    weaknesses: list[str]


class Feature(BaseModel):
    name: str
    description: str
    rice_score: Optional[float] = None


class FeatureList(BaseModel):
    core: list[Feature]
    nice_to_have: list[Feature] = Field(default_factory=list)


class ResearchBrief(BaseModel):
    problem_statement: str
    target_audience: str
    competitive_landscape: list[Competitor]
    feature_list: FeatureList
    technical_recommendations: str
    risks: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


class ValidatedIdea(BaseModel):
    idea: str
    confidence: float = Field(ge=0.0, le=1.0)
    differentiators: list[str]


class Task(BaseModel):
    id: str
    title: str
    description: str
    category: TaskCategory
    acceptance_criteria: list[str]
    estimated_hours: float
    dependencies: list[str] = Field(default_factory=list)


class SprintBacklog(BaseModel):
    tasks: list[Task]
    total_estimated_hours: float


class ColumnMapping(BaseModel):
    db_column: str
    db_type: str
    ui_field: str
    ui_type: str
    transform: str
    null_handling: str


class TableMapping(BaseModel):
    table_name: str
    columns: list[ColumnMapping]


class RPCMapping(BaseModel):
    function_name: str
    parameters: list[ColumnMapping]
    returns: list[ColumnMapping]


class MappingFunction(BaseModel):
    name: str
    from_type: str
    to_type: str
    used_by: list[str]


class SchemaContract(BaseModel):
    tables: list[TableMapping]
    rpcs: list[RPCMapping] = Field(default_factory=list)
    mapping_functions: list[MappingFunction]


class ArchitectureDoc(BaseModel):
    tech_stack: dict
    data_model: str
    api_contracts: str
    deployment_target: DeployTarget


class FileChange(BaseModel):
    path: str
    content: str
    action: FileAction


class CodeArtifact(BaseModel):
    files_changed: list[FileChange]
    test_results: Optional[str] = None
    schema_compliant: bool


class ReviewComment(BaseModel):
    file: str
    line: Optional[int] = None
    severity: str
    comment: str


class ReviewVerdict(BaseModel):
    status: ReviewStatus
    comments: list[ReviewComment]
    blocking_issues: list[str]


class Screenshot(BaseModel):
    name: str
    path: str
    viewport: Viewport
    theme: Theme


class CriterionResult(BaseModel):
    criterion: str
    status: CriterionStatus
    evidence: Optional[str] = None


class Issue(BaseModel):
    id: str
    severity: Severity
    description: str
    evidence: Optional[str] = None
    file: Optional[str] = None
    line: Optional[int] = None


class QAReport(BaseModel):
    overall_verdict: QAVerdict
    screenshots: list[Screenshot]
    issues: list[Issue]
    criteria_checked: list[CriterionResult]
    console_errors: list[str]
    accessibility_findings: list[str] = Field(default_factory=list)


class FixInstruction(BaseModel):
    issue_id: str
    instruction: str
    files_to_modify: list[str]


class RealityCheckVerdict(BaseModel):
    status: RealityStatus
    quality_rating: str
    issues: list[Issue]
    evidence_references: list[str]
    spec_compliance: list[CriterionResult]
    fix_instructions: list[FixInstruction] = Field(default_factory=list)


class DeploymentManifest(BaseModel):
    status: DeployStatus
    url: Optional[str] = None
    health_check_passed: bool
    rollback_instructions: str
    deployment_log: str
