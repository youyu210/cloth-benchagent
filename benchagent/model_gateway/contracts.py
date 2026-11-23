"""Provider-neutral contracts only. No SDK, credential handling or network calls."""

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol

SCHEMA_VERSION = "model-gateway-v1"

@dataclass(frozen=True)
class GatewayStatus:
    enabled: bool
    ready: bool
    provider: str
    capabilities: tuple[str, ...]
    message: str
    schema_version: str = SCHEMA_VERSION

@dataclass(frozen=True)
class PlanRequest:
    request_id: str
    user_text: str
    locale: str = "zh-CN"
    history: tuple[tuple[str, str], ...] = ()
    previous_plan: Mapping[str, Any] | None = None
    selected_scene: str | None = None

@dataclass(frozen=True)
class ExplainRequest:
    request_id: str
    report_id: str
    evidence_summary: Mapping[str, Any]
    locale: str = "zh-CN"

@dataclass(frozen=True)
class ModelResult:
    request_id: str
    status: Literal["disabled", "ok", "error"]
    message: str
    error_code: str | None = None
    proposed_plan: Mapping[str, Any] | None = None
    explanation: str | None = None
    provider_calls: int = 0
    diagnostics: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

@dataclass(frozen=True)
class ActionRequest:
    request_id: str
    objective: Mapping[str, Any]
    observation: Mapping[str, Any]
    allowed_actions: tuple[str, ...]


@dataclass(frozen=True)
class ScriptRequest:
    request_id: str
    contract: Mapping[str, Any]


class ModelGateway(Protocol):
    def get_status(self) -> GatewayStatus: ...
    def propose_plan(self, request: PlanRequest) -> ModelResult: ...
    def explain_report(self, request: ExplainRequest) -> ModelResult: ...
    def propose_action(self, request: ActionRequest) -> ModelResult: ...
    def propose_script(self, request: ScriptRequest) -> ModelResult: ...
