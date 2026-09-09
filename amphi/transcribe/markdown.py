"""Rendu du transcript en Markdown horodaté (en-tête + marqueurs + paragraphes)."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Marker, RecordingMeta
from .engine import Segment

_SENTENCE_END = (".", "!", "?", "…", ":", ".»", "?»", "!»", '."', '?"')


def hhmmss(seconds: float) -> str:
    return RecordingMeta.hhmmss(seconds)


def _yaml_value(v: object) -> str:
    s = str(v)
    if s == "":
        return '""'
    if any(c in s for c in ':#"\n') or s.strip() != s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _front_matter(meta: RecordingMeta, engine_desc: str) -> str:
    fields = {
        "titre": meta.titre,
        "matiere": meta.matiere,
        "niveau": meta.niveau,
        "date": meta.date,
        "langue": meta.langue,
        "duree": hhmmss(meta.duree_s),
        "marqueurs": len(meta.markers),
        "modele": engine_desc,
        "genere_par": "Amphi (transcription locale Faster-Whisper)",
    }
    lines = ["---"] + [f"{k}: {_yaml_value(v)}" for k, v in fields.items()] + ["---", ""]
    return "\n".join(lines)


def _paragraphs(
    segments: list[Segment],
    marker_offsets: list[float] | None = None,
    *,
    gap: float = 2.0,
    max_dur: float = 45.0,
    max_chars: int = 420,
) -> list[list[Segment]]:
    """Découpe en paragraphes. Un marqueur force une coupure : le paragraphe suivant
    commence à la parole qui suit le marqueur (le callout se pose juste avant)."""
    markers = sorted(marker_offsets or [])
    mi = 0
    paras: list[list[Segment]] = []
    cur: list[Segment] = []
    for i, seg in enumerate(segments):
        cur.append(seg)
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        if nxt is None:
            break

        crossed_marker = False
        while mi < len(markers) and markers[mi] <= seg.end:
            crossed_marker = True
            mi += 1

        dur = cur[-1].end - cur[0].start
        chars = sum(len(s.text) for s in cur)
        gap_after = nxt.start - seg.end
        ends_sentence = seg.text.rstrip().endswith(_SENTENCE_END)
        if (
            crossed_marker
            or gap_after >= gap
            or (ends_sentence and (dur >= max_dur or chars >= max_chars))
        ):
            paras.append(cur)
            cur = []
    if cur:
        paras.append(cur)
    return paras


def _para_text(para: list[Segment]) -> str:
    return " ".join(s.text.strip() for s in para).strip()


def _markers_section(markers: list[Marker], segments: list[Segment]) -> str:
    if not markers:
        return ""
    out = ["## Marqueurs", ""]
    for m in sorted(markers, key=lambda x: x.offset_s):
        snippet = _nearest_text(segments, m.offset_s)
        note = f" — {m.note}" if m.note else ""
        tail = f" · « …{snippet}… »" if snippet else ""
        out.append(f"- **[{hhmmss(m.offset_s)}]** {m.label}{note}{tail}")
    out += ["", "---", ""]
    return "\n".join(out)


def _nearest_text(segments: list[Segment], t: float, window: int = 90) -> str:
    for s in segments:
        if s.start <= t <= s.end:
            return s.text.strip()[:window]
    after = [s for s in segments if s.start >= t]
    if after:
        return after[0].text.strip()[:window]
    return segments[-1].text.strip()[:window] if segments else ""


def build_transcript_md(
    meta: RecordingMeta, segments: Iterable[Segment], *, engine_desc: str
) -> str:
    segs = [s for s in segments if s.text.strip()]
    segs.sort(key=lambda s: s.start)

    parts = [_front_matter(meta, engine_desc), f"# {meta.matiere} — {meta.titre}", ""]
    if not segs:
        parts.append("_(aucune parole détectée)_\n")
        return "\n".join(parts)

    ms = _markers_section(meta.markers, segs)
    if ms:
        parts.append(ms)

    markers = sorted(meta.markers, key=lambda m: m.offset_s)
    mi = 0
    for para in _paragraphs(segs, [m.offset_s for m in markers]):
        p_start = para[0].start
        # callouts pour tout marqueur situé avant le début de ce paragraphe
        while mi < len(markers) and markers[mi].offset_s <= p_start + 0.05:
            mk = markers[mi]
            parts.append(f"> ⭐ **[{hhmmss(mk.offset_s)}]** — marqueur « {mk.label} »"
                         + (f" — {mk.note}" if mk.note else ""))
            parts.append("")
            mi += 1
        parts.append(f"**[{hhmmss(p_start)}]** {_para_text(para)}")
        parts.append("")

    for mk in markers[mi:]:
        parts.append(f"> ⭐ **[{hhmmss(mk.offset_s)}]** — marqueur « {mk.label} »"
                     + (f" — {mk.note}" if mk.note else ""))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"
