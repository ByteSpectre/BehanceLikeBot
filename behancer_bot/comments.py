"""English comment bank for Behance comment tasks."""

from __future__ import annotations

import json
import random
from pathlib import Path

from .config import DATA_DIR

USED_COMMENTS_PATH = DATA_DIR / "used_comments.json"

_DETAILS = (
    "the spacing between sections",
    "the limited color palette",
    "the confident headline type",
    "the consistent image treatment",
    "the quiet background tones",
    "the clear case-study flow",
    "the careful use of contrast",
    "the simple icon language",
    "the strong opening composition",
    "the balance of text and visuals",
    "the crop and scale of the photography",
    "the way the grid holds the layout",
)

_FRAMES = (
    "I keep coming back to {d}: it makes the project feel deliberate and easy to follow.",
    "Really nice work. {D} gives the whole case a calm and professional tone.",
    "The concept lands well, and {d} keeps every screen looking cohesive.",
    "This feels polished. {D} is doing a lot of quiet work in the presentation.",
    "Strong project overall. {D} helps the story stay clear from start to finish.",
    "I like the discipline here. {D} never fights with the main idea.",
    "A clean result. {D} makes the portfolio piece feel finished rather than decorated.",
    "The visual system is convincing, especially {d}.",
    "Thoughtful art direction. {D} supports the message without adding noise.",
    "This is easy to read and pleasant to scan because of {d}.",
    "The craft shows. {D} feels consistent across the whole project.",
    "Nice sense of hierarchy. {D} tells me where to look first.",
    "I appreciate how controlled this is. {D} keeps the layout breathing.",
    "A memorable piece. {D} gives it a recognizable character.",
    "The presentation has a steady rhythm, and {d} is a big part of that.",
    "Well resolved. {D} makes the design feel intentional at every step.",
    "This would sit comfortably in a strong portfolio. {D} is particularly well handled.",
    "Clear, modern, and focused. {D} reinforces that impression.",
    "The details add up. {D} is one of the reasons the project feels premium.",
    "I enjoyed the pacing of this case study, especially {d}.",
    "Solid storytelling. {D} keeps the visuals and the idea aligned.",
    "The project has a confident tone. {D} makes that tone visible right away.",
    "Nothing here feels accidental. {D} is edited with a lot of care.",
    "A refined direction. {D} gives the work a distinct and lasting mood.",
    "Easy to understand at a glance, thanks in large part to {d}.",
    "The execution matches the concept. {D} is consistent and convincing.",
    "I like how quiet the design is. {D} still gives it enough personality.",
    "This is a strong example of restraint. {D} carries the identity cleanly.",
    "The case study is pleasant to move through. {D} creates a clear path.",
    "Beautiful control of emphasis. {D} keeps the focus on the right content.",
    "The project feels complete. {D} ties the separate frames together.",
    "Thoughtful and well composed. {D} is the detail I noticed first.",
)

_STANDALONE = (
    "The cover already explains the idea, and the rest of the case study keeps that promise.",
    "I like the pacing: each frame adds information instead of repeating the same layout.",
    "Color is used sparingly, which makes the accent moments feel earned.",
    "The mockups are convincing and sit naturally in the composition.",
    "Typography does the hard work here, and the images support it instead of competing.",
    "This feels like a complete brand story rather than a collection of isolated screens.",
    "The whitespace is generous, so the content never feels crowded.",
    "Small alignment details are consistent, and that is what makes the piece look expensive.",
    "I can follow the problem, the approach, and the result without getting lost.",
    "The photography style matches the graphic system, which is harder than it looks.",
    "A strong sense of scale: headlines, captions, and images each have a clear role.",
    "The palette is unusual in a good way and still stays readable.",
    "The still frames feel coherent with the static layouts around them.",
    "The poster series looks unified even though each piece can stand alone.",
    "Packaging views are shown clearly, and the graphic system survives on every surface.",
    "I appreciate the editorial layout. It gives the project a magazine-like confidence.",
    "The icons are simple enough to recognize and detailed enough to feel custom.",
    "Contrast is bold, but the text remains comfortable to read.",
    "This project has a point of view, and the visual choices all seem to share it.",
    "The ending frame is as considered as the opening, which makes the case feel finished.",
    "Grid, type, and image all agree with each other. That consistency is the real craft.",
    "I like that the design explains the product without needing a long explanation.",
    "The color story shifts slightly across frames and still feels like one family.",
    "Hierarchy is obvious in the first second, which is exactly what a portfolio piece needs.",
    "The layouts leave room for the work itself. Nothing is shouting over the content.",
    "A mature use of type: one or two families, clear sizes, and no unnecessary styles.",
    "The project photography has a consistent light and crop, so the sequence feels calm.",
    "I would remember this one. The opening composition is distinctive.",
    "Details like captions and margins are handled with the same care as the hero images.",
    "The case study reads quickly, but it still shows how the design decisions were made.",
    "There is a nice tension between strict structure and a few unexpected color accents.",
    "The interface screens look realistic, and the surrounding art direction supports them.",
    "This is the kind of presentation that makes the concept easy to trust.",
    "The visual rhythm changes just enough from frame to frame to stay interesting.",
    "I like the material choices in the mockups. They make the brand feel tangible.",
    "Text blocks are short and well placed, so the images can carry the atmosphere.",
    "The system looks flexible: it works on a poster, a screen, and a close-up detail.",
    "Nothing feels trendy for its own sake. The style serves the subject.",
    "A clear narrative arc from context to final design, presented with real control.",
    "The alignments stay clean from edge to edge, and that rewards a second look.",
    "The brand voice comes through in the type and the imagery at the same time.",
    "I like how the secondary information stays quiet until you decide to read it.",
    "The sequence builds confidence: research, direction, and then the finished system.",
    "Every frame has one job, and that makes the whole presentation feel focused.",
    "The color accents are rare enough that they actually guide attention.",
    "This has the clarity of a good editorial piece and the precision of a brand manual.",
    "The mockup lighting is consistent, so nothing pulls me out of the story.",
    "I can see the rules of the system and also where it is allowed to bend.",
    "A calm project with a sharp idea. The execution never loses that balance.",
    "The final applications prove the identity works outside the original poster.",
)


def _build_comments() -> tuple[str, ...]:
    comments = list(_STANDALONE)
    for detail in _DETAILS:
        capital = detail[0].upper() + detail[1:]
        for frame in _FRAMES:
            comments.append(frame.format(d=detail, D=capital))
    unique = tuple(dict.fromkeys(comments))
    if len(unique) != len(comments):
        raise RuntimeError("Словарь комментариев содержит повторы")
    short = [comment for comment in unique if len(comment) < 10]
    if short:
        raise RuntimeError("В словаре есть комментарий короче 10 символов")
    return unique


COMMENTS: tuple[str, ...] = _build_comments()


def pick_comment(path: Path | None = None) -> str:
    """Return a comment that has not been used yet, then remember it."""
    store = path or USED_COMMENTS_PATH
    used = _load_used(store)
    available = [comment for comment in COMMENTS if comment not in used]
    if not available:
        used = set()
        available = list(COMMENTS)
    choice = random.choice(available)
    used.add(choice)
    _save_used(store, used)
    return choice


def _load_used(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    if not isinstance(raw, list):
        return set()
    known = set(COMMENTS)
    return {item for item in raw if isinstance(item, str) and item in known}


def _save_used(path: Path, used: set[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(sorted(used), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return
