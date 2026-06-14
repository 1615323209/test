"""Chapter and batch analysis models."""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class RawChapter(BaseModel):
    """Parsed from raw novel text file."""
    index: int = Field(description="1-based chapter index")
    number: int = Field(description="Extracted chapter number (e.g. 1, 2, 3)")
    title: str = Field(description="Chapter title text")
    content: str = Field(description="Full chapter body text")
    char_count: int = Field(description="Character count of content")
    estimated_tokens: int = Field(description="Rough token estimate (chars * 0.5)")
    is_afterword: bool = Field(default=False, description="True for non-numbered afterword/author notes")
    start_line: int = Field(default=0, description="Line offset in source file")


class KeyEvent(BaseModel):
    """A single significant event in a chapter."""
    type: str = Field(description="Event type: 登场/退场/战斗/对话/转折/设定揭示/修炼/死亡/其他")
    description: str = Field(description="Brief description of the event")
    characters_involved: list[str] = Field(default_factory=list, description="Characters involved by name")
    significance: Literal["major", "minor", "transitional"] = Field(
        default="transitional",
        description="Plot significance level"
    )


class ChapterSummary(BaseModel):
    """Per-chapter structured summary (output of Layer 2)."""
    chapter_number: int = Field(description="Chapter number")
    title: str = Field(default="", description="Chapter title")
    summary: str = Field(description="Chapter summary, 100-200 Chinese characters")
    key_events: list[KeyEvent] = Field(default_factory=list, description="Key events this chapter")
    pov_character: str = Field(default="", description="Point-of-view character name")
    locations_visited: list[str] = Field(default_factory=list, description="Locations appearing this chapter")
    characters_appeared: list[str] = Field(default_factory=list, description="Characters appearing this chapter")
    reveals: list[str] = Field(default_factory=list, description="New information revealed this chapter")
    timeline_hint: str = Field(default="", description="Story timeline marker, e.g. '第3天' or '一年后'")
    emotional_tone: str = Field(default="", description="Emotional atmosphere: 紧张/轻松/悲伤/热血/悬疑/...")
    plot_tags: list[str] = Field(default_factory=list, description="Tags: 战斗/修炼/日常/冒险/政治/...")


class BatchNarrativeSummary(BaseModel):
    """Compressed summary of a batch, passed forward as rolling context."""
    batch_id: int
    chapter_range: tuple[int, int]
    summary: str = Field(description="200-400 character narrative summary of this batch")
    major_developments: list[str] = Field(default_factory=list, description="Major plot developments, 1 sentence each")
    arc_markers: list[str] = Field(default_factory=list, description="Story arc transition markers detected")


class BatchEntitySnapshot(BaseModel):
    """Entity state snapshot passed forward in rolling context."""
    characters: list[dict] = Field(default_factory=list, description="Known characters with key fields")
    factions: list[dict] = Field(default_factory=list, description="Known factions")
    locations: list[dict] = Field(default_factory=list, description="Known locations")
    powers: list[dict] = Field(default_factory=list, description="Known powers/abilities")
    unresolved_foreshadowing: list[dict] = Field(default_factory=list, description="Unresolved foreshadowing")
    total_entity_count: int = Field(default=0)


class BatchAnalysisOutput(BaseModel):
    """Complete output for a batch of chapters (Layer 2 result)."""
    batch_id: int = Field(description="Sequential batch number, starting from 1")
    chapter_range: tuple[int, int] = Field(description="(first_chapter, last_chapter) in this batch")

    # Narrative compression
    narrative_summary: BatchNarrativeSummary = Field(description="Compressed summary for rolling context")

    # Per-chapter detail
    chapters: list[ChapterSummary] = Field(description="Detailed summary for each chapter")

    # Entity updates
    entity_updates: BatchEntitySnapshot = Field(description="New and updated entities from this batch")

    # Cross-cutting
    foreshadowing: list[dict] = Field(
        default_factory=list,
        description="New foreshadowing: [{hint, chapter, likely_target}]"
    )
    contradictions_with_prior: list[dict] = Field(
        default_factory=list,
        description="Detected contradictions with previous batches: [{chapter, prior_chapter, description}]"
    )

    # Metadata
    batch_char_count: int = Field(default=0, description="Total characters in batch")
    batch_estimated_tokens: int = Field(default=0, description="Estimated input tokens")
