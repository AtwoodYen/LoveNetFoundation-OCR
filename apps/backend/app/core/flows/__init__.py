"""
处理流程模块
"""

from app.core.flows.base import TaskProcessingFlow, ProcessingContext, StepProgress, FlowFactory
from app.core.flows.pipeline_flow import PipelineFlow
from app.core.flows.client_vision_flow import ClientVisionFlow
from app.core.flows.google_vision_flow import GoogleVisionFlow

# 注册流程
FlowFactory.register_flow("pipeline", PipelineFlow)
FlowFactory.register_flow("client_vision", ClientVisionFlow)
FlowFactory.register_flow("google_vision", GoogleVisionFlow)

__all__ = [
    "TaskProcessingFlow",
    "ProcessingContext",
    "StepProgress",
    "FlowFactory",
]
