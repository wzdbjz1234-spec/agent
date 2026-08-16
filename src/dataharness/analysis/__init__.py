"""可审计分析运行时与结构化结果。"""

from .charts import (
    ChartArtifact,
    ChartSpecError,
    build_png_fallback,
    build_svg_fallback,
    chart_content_hash,
    validate_vega_lite_spec,
)
from .errors import (
    AnalysisBudgetError,
    AnalysisCircuitOpenError,
    AnalysisContextError,
    AnalysisError,
)
from .models import (
    AnalysisMode,
    AnalysisRequest,
    AnalysisSummary,
    FullProjectResult,
    InputReference,
    OutputInspection,
    OutputReference,
    OutputSpec,
    ProjectFileInspection,
    ProjectFileView,
)
from .runtime import AnalysisRuntime
from .verification import (
    EvidenceGate,
    ExecutionGate,
    FindingVerificationResult,
    GateName,
    GateReport,
    IntegrityGate,
    VerificationError,
    VerificationService,
)
from .warnings import DataWarning, DataWarningDetector, DataWarningKind

__all__ = [
    "AnalysisBudgetError",
    "AnalysisCircuitOpenError",
    "AnalysisContextError",
    "AnalysisError",
    "AnalysisMode",
    "AnalysisRequest",
    "AnalysisRuntime",
    "ChartArtifact",
    "ChartSpecError",
    "build_png_fallback",
    "build_svg_fallback",
    "chart_content_hash",
    "DataWarning",
    "DataWarningDetector",
    "DataWarningKind",
    "EvidenceGate",
    "ExecutionGate",
    "FindingVerificationResult",
    "GateName",
    "GateReport",
    "IntegrityGate",
    "AnalysisSummary",
    "FullProjectResult",
    "InputReference",
    "OutputReference",
    "OutputInspection",
    "OutputSpec",
    "ProjectFileInspection",
    "ProjectFileView",
    "VerificationError",
    "VerificationService",
    "validate_vega_lite_spec",
]
