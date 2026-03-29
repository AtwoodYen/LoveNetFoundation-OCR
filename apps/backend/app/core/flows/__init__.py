"""
处理流程模块
"""

from app.core.flows.base import TaskProcessingFlow, ProcessingContext, StepProgress, FlowFactory
from app.core.flows.pipeline_flow import PipelineFlow
from app.core.flows.client_vision_flow import ClientVisionFlow

# 注册流程
FlowFactory.register_flow("pipeline", PipelineFlow)
FlowFactory.register_flow("client_vision", ClientVisionFlow)

__all__ = [
    "TaskProcessingFlow",
    "ProcessingContext",
    "StepProgress",
    "FlowFactory",
]
