"""Layer 3: Aggregation — entity resolution, retcon detection, gap analysis."""
from .collator import collate_batch_results
from .resolver import resolve_entities
from .detector import detect_retcons, detect_gaps

__all__ = ["collate_batch_results", "resolve_entities", "detect_retcons", "detect_gaps"]
