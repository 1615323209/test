"""Pipeline state models for resume support."""

from pydantic import BaseModel, Field
from typing import Literal, Optional


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
    critical_events_log: list[dict] = Field(
        default_factory=list,
        description="Accumulated critical events across ALL batches, pruned to ~30 most recent"
    )
