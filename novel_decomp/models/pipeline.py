"""Pipeline state and checkpoint models for resume support."""

from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime


class LayerState(BaseModel):
    """State of a single pipeline layer."""
    layer: int = Field(description="Layer number (1-4)")
    status: Literal["pending", "running", "completed", "failed", "partial"] = "pending"
    batches_completed: int = Field(default=0, description="Number of batches processed")
    total_batches: int = Field(default=0, description="Total batches for this layer")
    error: str = Field(default="", description="Error message if failed")
    started_at: Optional[str] = Field(default=None, description="ISO timestamp when started")
    completed_at: Optional[str] = Field(default=None, description="ISO timestamp when completed")


class RollingContextState(BaseModel):
    """State of the rolling context accumulator during Layer 2."""
    current_batch_id: int = Field(default=0, description="Most recently completed batch")
    recent_summaries: list[dict] = Field(default_factory=list, description="Last 3 batch narrative summaries")
    arc_summaries: list[dict] = Field(default_factory=list, description="Compressed summaries for older batches")
    entity_snapshot: dict = Field(default_factory=dict, description="Current entity snapshot for next batch context")


class Checkpoint(BaseModel):
    """Full pipeline checkpoint for resume."""
    novel_path: str = Field(default="", description="Path to the novel being processed")
    layers: dict[str, LayerState] = Field(default_factory=dict, description="Layer states keyed by '1'-'4'")
    current_layer: int = Field(default=1, description="Currently active layer")
    rolling_context: RollingContextState = Field(default_factory=RollingContextState, description="Layer 2 rolling context")
    total_tokens_used: int = Field(default=0, description="Cumulative tokens used")
    total_input_tokens: int = Field(default=0, description="Cumulative input tokens")
    total_output_tokens: int = Field(default=0, description="Cumulative output tokens")
    total_cost_estimate: float = Field(default=0.0, description="Estimated cumulative cost in USD")
    created_at: str = Field(default="", description="ISO timestamp when pipeline started")
    updated_at: str = Field(default="", description="ISO timestamp when last saved")


class PipelineState(BaseModel):
    """Runtime pipeline state (in-memory, serialized to checkpoint)."""
    novel_path: str = ""
    layers: dict[str, LayerState] = Field(default_factory=dict)
    current_layer: int = 1
    rolling_context: RollingContextState = Field(default_factory=RollingContextState)
    started_at: str = ""
    updated_at: str = ""
    total_tokens_used: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_estimate: float = 0.0

    def to_checkpoint(self) -> Checkpoint:
        return Checkpoint(
            novel_path=self.novel_path,
            layers=self.layers,
            current_layer=self.current_layer,
            rolling_context=self.rolling_context,
            total_tokens_used=self.total_tokens_used,
            total_input_tokens=self.total_input_tokens,
            total_output_tokens=self.total_output_tokens,
            total_cost_estimate=self.total_cost_estimate,
            created_at=self.started_at,
            updated_at=self.updated_at,
        )
