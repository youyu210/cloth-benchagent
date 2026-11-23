"""Default placeholder: does not read credentials, import an SDK or send requests."""

from .contracts import ExplainRequest, GatewayStatus, ModelResult, PlanRequest

class DisabledGateway:
    def get_status(self) -> GatewayStatus:
        return GatewayStatus(
            enabled=False, ready=False, provider="disabled", capabilities=(),
            message="请在系统设置中保存 DeepSeek API 密钥，或使用手动表单配置。")

    def _result(self, request_id: str) -> ModelResult:
        return ModelResult(
            request_id=request_id, status="disabled",
            message="模型接口尚未启用，本次请求未发送至任何模型服务。",
            error_code="MODEL_NOT_CONFIGURED")

    def propose_plan(self, request: PlanRequest) -> ModelResult:
        return self._result(request.request_id)

    def explain_report(self, request: ExplainRequest) -> ModelResult:
        return self._result(request.request_id)

    def propose_action(self, request) -> ModelResult:
        return self._result(request.request_id)

    def propose_script(self, request) -> ModelResult:
        return self._result(request.request_id)
