"""Layer 4: Global synthesis — final outline, profiles, arcs."""
from .outline import build_full_outline
from .profiles import build_character_profiles, build_faction_profiles
from .arcs import synthesize_plot_arcs

__all__ = [
    "build_full_outline",
    "build_character_profiles",
    "build_faction_profiles",
    "synthesize_plot_arcs",
]
