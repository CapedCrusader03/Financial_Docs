"""Header-aware narrative extraction. Tables are removed before text splitting."""
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from app.config import settings

ITEM_HEADER = re.compile(
    r"^\s*(ITEM|Item)\s+(1A|1B|1C|1|2|3|4|5|6|7A|7|8|9A|9B|9C|10|11|12|13|14|15)\s*[.:-]\s*(.+)$"
)
WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class NarrativePart:
    section: str
    text: str


def _normalise(value: str) -> str:
    return WHITESPACE.sub(" ", value).strip()


def extract_sections(html: str) -> list[NarrativePart]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "table", "ix:header", "header", "footer"]):
        tag.decompose()

    blocks = [_normalise(tag.get_text(" ", strip=True)) for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "div"])]
    blocks = [block for block in blocks if len(block) > 2]
    sections: list[NarrativePart] = []
    current_header = "Cover and filing metadata"
    current: list[str] = []
    seen: set[tuple[str, str]] = set()

    def flush() -> None:
        text = "\n\n".join(current)
        key = (current_header, text[:200])
        if len(text) >= 120 and key not in seen:
            seen.add(key)
            sections.append(NarrativePart(current_header, text))

    for block in blocks:
        match = ITEM_HEADER.match(block)
        if match and len(block) < 300:
            flush()
            current.clear()
            current_header = f"Item {match.group(2)} - {match.group(3)}"
        elif not current or block != current[-1]:
            current.append(block)
    flush()
    return sections


def header_aware_chunks(section: NarrativePart, max_chars: int | None = None) -> list[NarrativePart]:
    """Split prose inside one mandated Item, preserving sentence boundaries and headers."""
    limit = max_chars or settings().narrative_chunk_chars
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", section.text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            # Long text is prose (tables were removed); split only on natural paragraph boundaries.
            paragraphs = sentence.split("\n\n")
        else:
            paragraphs = [sentence]
        for part in paragraphs:
            proposed = f"{current} {part}".strip()
            if current and len(proposed) > limit:
                chunks.append(current)
                current = part
            else:
                current = proposed
    if current:
        chunks.append(current)
    return [NarrativePart(section.section, chunk) for chunk in chunks if len(chunk) >= 80]
