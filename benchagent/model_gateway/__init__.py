"""Model gateway selection."""

import os

from .contracts import (
    SCHEMA_VERSION, ExplainRequest, GatewayStatus, ModelGateway, ModelResult, PlanRequest,
)
from .disabled import DisabledGateway

def get_default_gateway(api_key=None) -> ModelGateway:
    api_key = (api_key or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
    if not api_key:
        return DisabledGateway()
    from .deepseek import DeepSeekGateway
    return DeepSeekGateway(api_key)
