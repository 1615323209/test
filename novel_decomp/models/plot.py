"""Plot arc and beat models."""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class PlotBeat(BaseModel):
    """A single plot point within an arc."""
    chapter: int = Field(description="Chapter number where this beat occurs")
    summary: str = Field(description="One-sentence description of what happens")
    characters_involved: list[str] = Field(default_factory=list, description="Character IDs involved")
    significance: Literal["major", "minor", "transitional"] = Field(
        default="transitional",
        description="How important this beat is to the arc"
    )
    beat_type: str = Field(
        default="",
        description="Beat function: 铺垫/转折/高潮/收尾/揭示/伏笔"
    )


class PlotArc(BaseModel):
    """A complete story arc spanning multiple chapters."""
    id: str = Field(description="Stable arc ID: arc_NNN")
    name: str = Field(description="Arc name, e.g. '废墟试炼篇'")
    arc_type: str = Field(
        default="",
        description="Arc type: 开篇/修炼/冒险/战争/解密/感情/过渡/终章"
    )
    start_chapter: int = Field(description="First chapter of this arc")
    end_chapter: int = Field(description="Last chapter of this arc")
    description: str = Field(description="200-400 character arc summary")
    beats: list[PlotBeat] = Field(default_factory=list, description="Key plot beats in this arc")
    primary_characters: list[str] = Field(default_factory=list, description="Main character IDs in this arc")
    primary_factions: list[str] = Field(default_factory=list, description="Main faction IDs in this arc")
    main_locations: list[str] = Field(default_factory=list, description="Main location IDs used in this arc")
    parent_arc_id: str = Field(default="", description="Parent arc ID for hierarchical arcs (empty = top-level)")
    sub_arc_count: int = Field(default=0, description="Number of child arcs")
    emotional_peak_chapter: int = Field(default=0, description="Chapter where arc climaxes emotionally")
    resolution_chapter: int = Field(default=0, description="Chapter where arc is resolved")
    is_complete: bool = Field(default=True, description="Whether the arc has concluded")


class ArcConnection(BaseModel):
    """Connection between two plot arcs."""
    source_arc: str = Field(description="Source arc ID")
    target_arc: str = Field(description="Target arc ID")
    connection_type: str = Field(
        description="Connection type: 因果/承接/并列/对比/伏笔回收/..."
    )
    description: str = Field(default="", description="How these arcs connect")
