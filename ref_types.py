"""Type definitions ported from Zotero Reference typing/global.d.ts."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PDFItem:
    """Equivalent to PDF.js getTextContent() item, populated from PyMuPDF span."""
    str: str
    transform: list[float]  # [scaleX, skewX, skewY, scaleY, translateX, translateY]
    width: float
    height: float
    font_name: str = ""
    url: Optional[str] = None


@dataclass
class PDFLine:
    """Merged items at the same Y coordinate."""
    x: float
    y: float
    text: str
    height: float
    width: float
    url: Optional[str] = None
    _height: list[float] = field(default_factory=list)  # heights of constituent items
    _x: Optional[float] = None  # original x before indent normalization
    _offset: Optional[float] = None
    column: int = 0
    page_num: int = 0
    same: Optional["PDFLine"] = None  # reference to duplicate line on another page


@dataclass
class ItemBaseInfo:
    """Basic reference information."""
    identifiers: dict[str, str] = field(default_factory=dict)
    title: str = ""
    authors: list[str] = field(default_factory=list)
    type: str = "journalArticle"
    text: str = ""
    year: Optional[str] = None
    url: Optional[str] = None
    number: Optional[int] = None


@dataclass
class ItemInfo(ItemBaseInfo):
    """Full reference information with metadata."""
    publish_date: Optional[str] = None
    abstract: Optional[str] = None
    primary_venue: Optional[str] = None
    source: Optional[str] = None
    tags: list = field(default_factory=list)
    references: list[ItemBaseInfo] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    description: Optional[str] = None
