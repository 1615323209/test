"""Checkpoint management for pipeline resume support.

Saves and loads full pipeline state, including Layer 2 rolling context,
so long-running operations can be interrupted and resumed.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional


class CheckpointManager:
    """Manages pipeline checkpoint state for resume support."""

    def __init__(self, checkpoint_dir: Path):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.checkpoint_dir / "pipeline_state.json"

    def save(self, state: dict):
        """Save full pipeline state to checkpoint file.

        Args:
            state: Dictionary with pipeline state, must include:
                - novel_path
                - layers: dict of layer states
                - current_layer
                - rolling_context (optional, for Layer 2 resume)
                - usage (optional)
        """
        state["updated_at"] = datetime.now().isoformat()
        if "created_at" not in state:
            state["created_at"] = state["updated_at"]

        self._state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self) -> Optional[dict]:
        """Load saved pipeline state.

        Returns:
            State dict if checkpoint exists, None otherwise.
        """
        if not self._state_file.exists():
            return None
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def get_layer_state(self, layer: int) -> Optional[dict]:
        """Get state for a specific layer.

        Args:
            layer: Layer number (1-4).

        Returns:
            Layer state dict or None.
        """
        state = self.load()
        if not state:
            return None
        layers = state.get("layers", {})
        return layers.get(str(layer))

    def update_layer_state(
        self,
        layer: int,
        *,
        status: str = "",
        batches_completed: Optional[int] = None,
        total_batches: Optional[int] = None,
        error: str = "",
        **kwargs,
    ):
        """Update the state of a single layer and save.

        Args:
            layer: Layer number (1-4).
            status: New status ('pending'/'running'/'completed'/'failed'/'partial').
            batches_completed: Number of batches completed.
            total_batches: Total number of batches.
            error: Error message if failed.
            **kwargs: Additional state fields to update.
        """
        state = self.load() or {
            "novel_path": "",
            "layers": {},
            "current_layer": layer,
            "created_at": datetime.now().isoformat(),
            "total_tokens_used": 0,
            "total_cost_estimate": 0.0,
        }

        layer_key = str(layer)
        if layer_key not in state["layers"]:
            state["layers"][layer_key] = {
                "layer": layer,
                "status": "pending",
                "batches_completed": 0,
                "total_batches": 0,
                "started_at": None,
                "completed_at": None,
                "error": "",
            }

        ls = state["layers"][layer_key]

        if status:
            ls["status"] = status
            if status == "running" and not ls.get("started_at"):
                ls["started_at"] = datetime.now().isoformat()
            if status in ("completed", "failed", "partial"):
                ls["completed_at"] = datetime.now().isoformat()

        if batches_completed is not None:
            ls["batches_completed"] = batches_completed
        if total_batches is not None:
            ls["total_batches"] = total_batches
        if error:
            ls["error"] = error

        ls.update(kwargs)

        if layer > state["current_layer"]:
            state["current_layer"] = layer

        self.save(state)

    def save_rolling_context(self, context: dict):
        """Save Layer 2 rolling context for resume."""
        state = self.load() or {}
        state["rolling_context"] = context
        self.save(state)

    def load_rolling_context(self) -> Optional[dict]:
        """Load Layer 2 rolling context."""
        state = self.load()
        return state.get("rolling_context") if state else None

    def update_usage(self, usage: dict):
        """Update cumulative token/cost tracking."""
        state = self.load()
        if state:
            state["total_tokens_used"] = usage.get("total_tokens", 0)
            state["total_cost_estimate"] = usage.get("estimated_cost_usd", 0)
            self.save(state)

    def reset(self):
        """Delete all checkpoint state."""
        if self._state_file.exists():
            self._state_file.unlink()
        # Also delete layer-specific checkpoints
        for f in self.checkpoint_dir.glob("layer*_checkpoint.json"):
            f.unlink()

    @property
    def has_checkpoint(self) -> bool:
        """Check if a checkpoint exists."""
        return self._state_file.exists()
