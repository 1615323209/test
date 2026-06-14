"""Entity models: Character, Faction, Location, Power, Relationship."""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class EntityBase(BaseModel):
    """Common fields for all entities."""
    id: str = Field(description="Stable canonical ID (UUID format, prefixed: char_/fact_/loc_/pow_)")
    canonical_name: str = Field(description="Primary/standardized name")
    aliases: list[str] = Field(default_factory=list, description="All alternative names, nicknames, titles")
    first_appearance: int = Field(description="Chapter number of first appearance")
    last_appearance: int = Field(description="Latest chapter seen (so far)")
    description: str = Field(default="", description="Brief description of the entity")
    status: Literal["active", "deceased", "missing", "destroyed", "unknown"] = Field(
        default="active",
        description="Current known status"
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence in entity existence (0-1)")


class Relationship(BaseModel):
    """Relationship between two characters."""
    target_id: str = Field(description="ID of the related entity")
    target_name: str = Field(default="", description="Name of the related entity")
    relation_type: str = Field(description="Relation type: 师徒/敌对/血亲/朋友/恋人/同盟/上下级/利益/...")
    description: str = Field(default="", description="Brief description of the relationship dynamic")
    first_established: int = Field(description="Chapter where relationship is first established")
    last_updated: int = Field(default=0, description="Chapter of most recent relationship change")
    current_state: str = Field(default="", description="Current state of the relationship")


class CharacterAppearance(BaseModel):
    """A single chapter appearance record for a character."""
    chapter: int
    role: Literal["protagonist", "antagonist", "supporting", "cameo", "mentioned"] = "supporting"
    state: str = Field(default="", description="Character state: 正常/重伤/伪装/升级/昏迷/...")
    note: str = Field(default="", description="Brief note about what happened to them")


class Character(EntityBase):
    """Full character profile after entity resolution."""
    type: Literal["character"] = "character"
    gender: str = Field(default="", description="性别")
    age_hint: str = Field(default="", description="Age description: '16岁左右' / '中年' / 'unknown'")
    role_type: Literal["主角", "配角", "反派", "路人", "导师", "恋人", "家人", "其他"] = Field(
        default="配角", description="Narrative role type"
    )
    faction_affiliations: list[str] = Field(default_factory=list, description="Faction IDs this character belongs to")
    powers: list[str] = Field(default_factory=list, description="Power/ability IDs possessed")
    personality_traits: list[str] = Field(default_factory=list, description="Personality trait keywords")
    goals: list[str] = Field(default_factory=list, description="Character goals and motivations")
    relationships: list[Relationship] = Field(default_factory=list, description="All known relationships")
    appearance_timeline: list[CharacterAppearance] = Field(default_factory=list, description="Chapter-by-chapter appearance record")
    character_arc: str = Field(default="", description="Narrative summary of character's journey (200-500 chars)")
    image_hints: list[str] = Field(default_factory=list, description="Visual description hints for art generation")


class FactionEvent(BaseModel):
    """A significant event in a faction's timeline."""
    chapter: int
    event: str = Field(description="Description of the event")
    event_type: str = Field(default="", description="Event type: 成立/扩张/衰落/覆灭/内乱/结盟/宣战/...")


class Faction(EntityBase):
    """Full faction/organization profile."""
    type: Literal["faction"] = "faction"
    faction_type: str = Field(default="", description="Faction category: 宗门/家族/国家/组织/商会/佣兵团/...")
    leader: str = Field(default="", description="Current leader name or character ID")
    leader_id: str = Field(default="", description="Leader's character ID if known")
    members: list[str] = Field(default_factory=list, description="Character IDs of known members")
    member_names: list[str] = Field(default_factory=list, description="Member names (for display)")
    ideology: str = Field(default="", description="Faction ideology or creed")
    goals: list[str] = Field(default_factory=list, description="Faction goals")
    territory: str = Field(default="", description="Territory or headquarters description")
    internal_conflicts: list[str] = Field(default_factory=list, description="Internal conflicts and tensions")
    allies: list[str] = Field(default_factory=list, description="Allied faction IDs")
    enemies: list[str] = Field(default_factory=list, description="Enemy faction IDs")
    timeline: list[FactionEvent] = Field(default_factory=list, description="Key events in faction history")
    strength_hint: str = Field(default="", description="Power/influence estimate: '顶级宗门' / '三流家族' / ...")


class Location(EntityBase):
    """A location in the story world."""
    type: Literal["location"] = "location"
    location_type: str = Field(default="", description="Location type: 城市/宗门/秘境/星球/建筑/国家/...")
    parent_location: str = Field(default="", description="Parent location name or ID")
    significance: str = Field(default="", description="Why this location matters to the story")
    chapters_present: list[int] = Field(default_factory=list, description="Chapters where this location appears")
    affiliated_factions: list[str] = Field(default_factory=list, description="Faction IDs controlling or based here")
    features: list[str] = Field(default_factory=list, description="Notable features or landmarks")
    map_hints: str = Field(default="", description="Geographic positioning hints")


class Power(EntityBase):
    """A power, ability, technique, or magic system element."""
    type: Literal["power"] = "power"
    power_category: str = Field(default="", description="Category: 境界/功法/武技/法术/天赋/丹药/法宝/系统/...")
    users: list[str] = Field(default_factory=list, description="Character IDs who possess this power")
    user_names: list[str] = Field(default_factory=list, description="User names for display")
    tiers: list[str] = Field(default_factory=list, description="Levels/ranks/tiers if structured, e.g. ['筑基','金丹','元婴']")
    limitations: list[str] = Field(default_factory=list, description="Known limitations, costs, or weaknesses")
    source: str = Field(default="", description="Origin of this power: 传承/觉醒/炼制/兑换/...")
    mechanics: str = Field(default="", description="How the power works mechanically")
