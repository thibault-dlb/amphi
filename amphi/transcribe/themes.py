"""Index des thèmes par matière — 2ᵉ requête Gemini, enchaînée après le résumé.

Pour chaque cours, Gemini reçoit la liste des thèmes déjà indexés dans la matière et la
transcription aux lignes numérotées. Il ne renvoie que l'apport de CE cours (thèmes
existants ou nouveaux, passages avec plage de lignes, nature et note) ; la fusion se fait
ici, si bien que les références des cours précédents ne repassent jamais par le modèle.

    <matière>/INDEX.md                            lu par un agent IA, réécrit à chaque mise à jour
    <matière>/Enregistrements/index_themes.json   données source de INDEX.md

Rattrapage des cours déjà transcrits :

    python -m amphi.transcribe.themes --all [--matiere NOM] [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import logging
import os
import re
import sys
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..config import Config, GeminiConfig
from ..library import scan
from ..logging_setup import setup
from ..models import RecordingMeta
from ..paths import INDEX_LOCK
from .summarize import SummaryError, SummarySkipped, generate, strip_front_matter

log = logging.getLogger(__name__)

INDEX_MD = "INDEX.md"
INDEX_JSON = "index_themes.json"
VERSION = 1
MAX_KEYWORDS = 10
MAX_NOTE_CHARS = 200
LOCK_TIMEOUT_S = 90.0
LOCK_STALE_S = 60.0

# L'ordre sert de priorité quand deux plages qui se chevauchent sont fusionnées.
NATURES = ("définition", "explication", "exemple", "exercice", "rappel", "mention")
_NATURE_PREFIXES = (
    ("defin", "définition"),
    ("expli", "explication"),
    ("demonstr", "explication"),
    ("exemp", "exemple"),
    ("illustr", "exemple"),
    ("exerc", "exercice"),
    ("rappel", "rappel"),
    ("recap", "rappel"),
)

# Paragraphe du transcript : « **[hh:mm:ss]** texte », un par ligne (cf. markdown.py).
_PARA = re.compile(r"^\*\*\[(\d{2}:\d{2}:\d{2})\]\*\*\s*(.*)$")

SYSTEM_PROMPT = """\
Tu construis, cours après cours, l'index des thèmes d'une matière. Un agent IA s'en sert pour \
retrouver précisément où chaque notion est traitée dans les transcriptions des cours.

Tu reçois :
1. la liste des thèmes DÉJÀ indexés pour cette matière (id · nom · mots-clés — description), \
éventuellement vide ;
2. la transcription automatique (donc imparfaite) d'UN nouveau cours. Chaque ligne commence par \
« L<n> », son numéro de ligne dans le fichier, suivi de son horodatage.

Renvoie chaque thème abordé dans CE cours, avec les passages où il l'est :
- thème déjà indexé : reprends son id et son nom exacts ; tu peux ajouter des mots-clés propres \
à ce cours ;
- nouveau thème : id = 0, nom court et précis (2 à 6 mots), description d'une phrase, 3 à 8 \
mots-clés (synonymes français/anglais, sigles, termes employés par le prof, y compris tels que la \
transcription les déforme).

Règles :
- Réutilise un thème existant dès qu'il s'agit de la même notion, même dite en anglais ou \
autrement. Aucun doublon.
- Granularité : une notion qu'un étudiant chercherait en révisant (ex. « Régression linéaire », \
« Méthode des moindres carrés », « Apprentissage supervisé »). Ni le nom de la matière, ni un \
détail d'une phrase.
- debut et fin sont des numéros L<n> présents dans la transcription (debut ≤ fin) ; un passage \
couvre des lignes consécutives consacrées au thème. Un même passage peut relever de plusieurs \
thèmes. Inclus aussi les rappels et les mentions utiles.
- nature : définition, explication, exemple, exercice, rappel ou mention.
- note : 15 mots au plus, ce que contient concrètement le passage, fidèle au texte.
- Organisation du cours, évaluation, examens, projets à rendre : un seul thème « Organisation du \
cours et évaluation ». Ignore les bavardages.
- N'invente rien : uniquement ce qui est dans la transcription. Au plus 40 thèmes pour ce cours.

Réponds uniquement en JSON, sous la forme :
{"themes": [{"id": 0, "nom": "…", "description": "…", "mots_cles": ["…"], \
"passages": [{"debut": 12, "fin": 18, "nature": "définition", "note": "…"}]}]}
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "themes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {
                        "type": "INTEGER",
                        "description": "id d'un thème déjà indexé, 0 pour un nouveau thème",
                    },
                    "nom": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "mots_cles": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "passages": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "debut": {"type": "INTEGER"},
                                "fin": {"type": "INTEGER"},
                                "nature": {"type": "STRING", "description": " | ".join(NATURES)},
                                "note": {"type": "STRING"},
                            },
                            "required": ["debut", "fin", "nature", "note"],
                            "property_ordering": ["debut", "fin", "nature", "note"],
                        },
                    },
                },
                "required": ["id", "nom", "passages"],
                "property_ordering": ["id", "nom", "description", "mots_cles", "passages"],
            },
        },
    },
    "required": ["themes"],
}


# --------------------------------------------------------------------------- #
#  Transcript                                                                 #
# --------------------------------------------------------------------------- #
def numbered_lines(md: str) -> list[tuple[int, str, str]]:
    """(n° de ligne, horodatage, texte) de chaque paragraphe horodaté.

    Découpe sur LF et non avec splitlines(), qui coupe aussi sur d'autres séparateurs
    Unicode : la numérotation doit rester celle de l'éditeur et des outils de lecture.
    """
    out: list[tuple[int, str, str]] = []
    for n, line in enumerate(md.split("\n"), start=1):
        m = _PARA.match(line.rstrip("\r"))
        if m:
            out.append((n, m.group(1), m.group(2).strip()))
    return out


def _read(transcript: Path) -> tuple[str, str]:
    """(texte, sha1). Lecture binaire : aucune conversion des fins de ligne."""
    raw = transcript.read_bytes()
    return raw.decode("utf-8"), hashlib.sha1(raw).hexdigest()


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _has_speech(md: str) -> bool:
    """Même règle que pour le résumé (worker._maybe_summarize)."""
    body = strip_front_matter(md)
    return "aucune parole détectée" not in body and len(body.strip()) >= 150


def _locations(transcript: Path) -> tuple[Path, Path, Path]:
    """(dossier de la matière, index_themes.json, INDEX.md) du cours de ce transcript."""
    recordings = transcript.parent.parent
    subject = recordings.parent
    return subject, recordings / INDEX_JSON, subject / INDEX_MD


def course_key(transcript: Path) -> str:
    """Clé d'un cours dans l'index : le nom de son dossier (<date>_<titre>, unique)."""
    return transcript.parent.name


# --------------------------------------------------------------------------- #
#  Prompt et réponse                                                          #
# --------------------------------------------------------------------------- #
def build_prompt(index: dict, meta: RecordingMeta, numbered: list[tuple[int, str, str]]) -> str:
    if index["themes"]:
        known = "\n".join(
            f"{t['id']} · {t['nom']}"
            + (f" · {', '.join(t['mots_cles'])}" if t.get("mots_cles") else "")
            + (f" — {t['description']}" if t.get("description") else "")
            for t in index["themes"]
        )
    else:
        known = "(aucun : premier cours indexé pour cette matière)"
    body = "\n".join(f"L{n} [{stamp}] {text}" for n, stamp, text in numbered)
    return (
        f"Matière : {meta.matiere} ({meta.niveau})\n"
        f"Cours à indexer : {meta.date} — {meta.titre} "
        f"(durée {RecordingMeta.hhmmss(meta.duree_s)})\n\n"
        f"Thèmes déjà indexés (id · nom · mots-clés — description) :\n{known}\n\n"
        f"Transcription numérotée (L<n> = numéro de ligne dans transcript.md) :\n\n{body}"
    )


def parse_reply(reply: str, model: str) -> list[dict]:
    """Thèmes de la réponse JSON (tolère des balises ``` ou du texte autour)."""
    text = reply.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[start : end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            data = None
    themes = data.get("themes") if isinstance(data, dict) else data
    if not isinstance(themes, list):
        raise SummaryError(f"réponse JSON illisible de {model} ({len(reply)} caractères)")
    return [t for t in themes if isinstance(t, dict)]


# --------------------------------------------------------------------------- #
#  Fusion                                                                     #
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    """Clé de comparaison : sans casse, sans accents, ponctuation réduite à un espace."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]+", " ", text.casefold()).strip()


def _clean(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _as_int(value: object) -> int | None:
    if isinstance(value, str):
        value = value.strip().lstrip("Ll")  # « L58 » quand le schéma n'a pas été appliqué
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _nature(value: object) -> str:
    key = _norm(str(value or ""))
    for prefix, nature in _NATURE_PREFIXES:
        if key.startswith(prefix):
            return nature
    return "mention"


def _clean_passages(raw: object, stamps: dict[int, str], lines: list[int]) -> list[dict]:
    """Passages valides, bornés au transcript et recalés sur des lignes de paragraphe."""
    out: list[dict] = []
    if not isinstance(raw, list) or not lines:
        return out
    for p in raw:
        if not isinstance(p, dict):
            continue
        a, b = _as_int(p.get("debut")), _as_int(p.get("fin"))
        if a is None and b is None:
            continue
        a, b = (b if a is None else a), (a if b is None else b)
        if a > b:
            a, b = b, a
        if b < lines[0] or a > lines[-1]:
            continue  # hors du transcript (en-tête, ou numéro inventé)
        i = bisect.bisect_left(lines, a)       # 1er paragraphe >= a
        j = bisect.bisect_right(lines, b) - 1  # dernier paragraphe <= b
        if i > j:
            i = j  # plage tombée entre deux paragraphes (ligne vide) : celui qui précède
        out.append({
            "debut": lines[i],
            "fin": lines[j],
            "horodatage": stamps[lines[i]],
            "nature": _nature(p.get("nature")),
            "note": _clean(p.get("note"), MAX_NOTE_CHARS),
        })
    return out


def _merge_refs(refs: list[dict]) -> list[dict]:
    """Trie par cours puis par ligne ; fusionne les plages d'un même cours qui se chevauchent
    (notes jointes). Deux passages simplement voisins restent distincts."""
    rank = {nature: i for i, nature in enumerate(NATURES)}
    out: list[dict] = []
    for ref in sorted(refs, key=lambda r: (r["cours"], r["debut"], r["fin"])):
        prev = out[-1] if out else None
        if prev and prev["cours"] == ref["cours"] and ref["debut"] <= prev["fin"]:
            prev["fin"] = max(prev["fin"], ref["fin"])
            if rank.get(ref["nature"], len(rank)) < rank.get(prev["nature"], len(rank)):
                prev["nature"] = ref["nature"]
            if ref["note"] and ref["note"] not in prev["note"]:
                joined = prev["note"].rstrip(". ")
                prev["note"] = f"{joined} ; {ref['note']}" if joined else ref["note"]
        else:
            out.append(dict(ref))
    return out


def _merge_keywords(current: list[str], new: object, nom: str) -> list[str]:
    seen = {_norm(nom), *(_norm(k) for k in current)}
    out = list(current)
    for kw in new if isinstance(new, list) else []:
        text = _clean(kw, 60)
        key = _norm(text)
        if key and key not in seen and len(out) < MAX_KEYWORDS:
            out.append(text)
            seen.add(key)
    return out


def new_index(matiere: str, niveau: str) -> dict:
    return {
        "version": VERSION,
        "matiere": matiere,
        "niveau": niveau,
        "mis_a_jour": "",
        "prochain_id": 1,  # jamais réutilisé, même quand un thème disparaît
        "cours": {},
        "themes": [],
    }


def load_index(json_path: Path, matiere: str = "", niveau: str = "") -> dict:
    """Index d'une matière (vide s'il n'existe pas encore)."""
    if not json_path.exists():
        return new_index(matiere, niveau)
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SummaryError(f"{json_path} inaccessible ({exc})") from exc
    except ValueError as exc:
        raise SummaryError(
            f"{json_path} illisible ({exc}) — le supprimer puis relancer l'indexation des cours"
        ) from exc
    if not isinstance(data, dict):
        raise SummaryError(f"{json_path} n'est pas un index des thèmes")
    for key, value in new_index(matiere, niveau).items():
        data.setdefault(key, value)
    highest = max((t.get("id", 0) for t in data["themes"]), default=0)
    data["prochain_id"] = max(data["prochain_id"], highest + 1)
    return data


def merge_course(
    index: dict, key: str, course: dict, items: list[dict], numbered: list[tuple[int, str, str]]
) -> int:
    """Intègre la réponse de Gemini pour le cours `key`. Renvoie le nombre de thèmes concernés."""
    lines = [n for n, _, _ in numbered]
    stamps = {n: stamp for n, stamp, _ in numbered}
    for theme in index["themes"]:  # réindexation : les anciens passages de ce cours disparaissent
        theme["refs"] = [r for r in theme.get("refs", []) if r.get("cours") != key]
    by_id = {t["id"]: t for t in index["themes"]}
    by_name = {_norm(t["nom"]): t for t in index["themes"]}

    touched: dict[int, dict] = {}
    for item in items:
        passages = _clean_passages(item.get("passages"), stamps, lines)
        if not passages:
            continue
        nom = _clean(item.get("nom"), 120)
        # le nom exact prime sur l'id : un id recopié de travers viserait un autre thème
        theme = by_name.get(_norm(nom)) or by_id.get(_as_int(item.get("id")))
        if theme is None:
            if not _norm(nom):
                continue
            theme = {
                "id": index["prochain_id"],
                "nom": nom,
                "description": _clean(item.get("description"), 300),
                "mots_cles": [],
                "refs": [],
            }
            index["prochain_id"] += 1
            index["themes"].append(theme)
            by_id[theme["id"]] = theme
            by_name[_norm(nom)] = theme
        theme["mots_cles"] = _merge_keywords(theme["mots_cles"], item.get("mots_cles"), theme["nom"])
        theme["refs"].extend({"cours": key, **p} for p in passages)
        touched[theme["id"]] = theme

    for theme in touched.values():
        theme["refs"] = _merge_refs(theme["refs"])
    index["themes"] = [t for t in index["themes"] if t["refs"]]
    index["cours"][key] = course
    return len(touched)


def prune_missing(index: dict, subject: Path) -> list[str]:
    """Retire de l'index les cours dont le transcript a disparu (dossier supprimé ou renommé)."""
    gone = [
        k for k, c in index["cours"].items() if not (subject / c.get("transcript", "")).is_file()
    ]
    if gone:
        for theme in index["themes"]:
            theme["refs"] = [r for r in theme["refs"] if r["cours"] not in gone]
        index["themes"] = [t for t in index["themes"] if t["refs"]]
        for k in gone:
            del index["cours"][k]
    return gone


# --------------------------------------------------------------------------- #
#  Rendu                                                                      #
# --------------------------------------------------------------------------- #
def _recorded_at(transcript: Path) -> float:
    """Heure d'enregistrement du cours (meta.json), pour départager deux cours du même jour."""
    try:
        meta = json.loads((transcript.parent / "meta.json").read_text(encoding="utf-8"))
        return float(meta.get("created_at", 0.0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def render_markdown(index: dict, subject: Path) -> str:
    """INDEX.md : table des cours (codes C1, C2… par date puis heure d'enregistrement), puis une
    section par thème dans l'ordre de première apparition, une puce par passage."""
    courses = sorted(
        index["cours"].items(),
        key=lambda kv: (
            kv[1].get("date", ""),
            _recorded_at(subject / kv[1].get("transcript", "")),
            kv[0],
        ),
    )
    code = {key: f"C{i}" for i, (key, _) in enumerate(courses, start=1)}
    order = {key: i for i, (key, _) in enumerate(courses)}

    def position(ref: dict) -> tuple[int, int]:
        return order.get(ref["cours"], len(order)), ref["debut"]

    out = [
        "---",
        f"matiere: {json.dumps(index['matiere'], ensure_ascii=False)}",
        f"niveau: {json.dumps(index['niveau'], ensure_ascii=False)}",
        f"cours_indexes: {len(courses)}",
        f"themes: {len(index['themes'])}",
        f"mis_a_jour: {index['mis_a_jour']}",
        'genere_par: "Amphi (index des thèmes, Gemini)"',
        "---",
        "",
        f"# {index['matiere']} — Index des thèmes",
        "",
        "> Généré par Amphi après chaque cours — ne pas modifier (fichier réécrit).",
        ">",
        "> - Une puce = un passage : `C<n> L<début>–<fin> [hh:mm:ss] nature — note`.",
        "> - `L…` = lignes du `transcript.md` du cours (1 = première ligne du fichier) ;"
        " `[hh:mm:ss]` = position dans l'audio.",
        "> - Transcription automatique : vérifier sur le texte avant de s'y fier.",
        "",
        "## Cours",
        "",
        "| Code | Date | Titre | Transcript |",
        "|---|---|---|---|",
    ]
    for key, course in courses:
        rel = course.get("transcript", "")
        path = subject / rel
        if not path.is_file():
            flag = " ⚠ introuvable"
        elif _sha1(path) != course.get("sha1"):
            flag = " ⚠ modifié depuis l'indexation"
        else:
            flag = ""
        titre = str(course.get("titre", "")).replace("|", "\\|")
        out.append(f"| {code[key]} | {course.get('date', '')} | {titre} | `{rel}`{flag} |")

    out += ["", "## Thèmes", ""]
    first_seen = lambda t: min(map(position, t["refs"]), default=(len(order), 0))  # noqa: E731
    for theme in sorted(index["themes"], key=first_seen):
        out += [f"### {theme['nom']}", ""]
        if theme.get("description"):
            out += [theme["description"], ""]
        if theme.get("mots_cles"):
            out += ["Mots-clés : " + ", ".join(theme["mots_cles"]), ""]
        for ref in sorted(theme["refs"], key=position):
            span = f"L{ref['debut']}"
            if ref["fin"] != ref["debut"]:
                span += f"–{ref['fin']}"
            stamp = f" [{ref['horodatage']}]" if ref.get("horodatage") else ""
            note = f" — {ref['note']}" if ref.get("note") else ""
            out.append(f"- {code.get(ref['cours'], '?')} {span}{stamp} {ref['nature']}{note}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
#  Écriture                                                                   #
# --------------------------------------------------------------------------- #
@contextmanager
def _index_lock():
    """Verrou court entre processus (worker, rattrapage) autour de lecture-fusion-écriture.
    L'appel Gemini, long, se fait avant, hors verrou."""
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(INDEX_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                stale = time.time() - INDEX_LOCK.stat().st_mtime > LOCK_STALE_S
            except OSError:
                stale = False  # retiré entre-temps : on retente
            if stale:  # processus mort en tenant le verrou
                try:
                    INDEX_LOCK.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() > deadline:
                raise SummaryError("index des thèmes verrouillé par un autre processus") from None
            time.sleep(0.2)
    try:
        yield
    finally:
        try:
            INDEX_LOCK.unlink()
        except OSError:
            pass


def _write_atomic(path: Path, text: str, attempts: int = 5) -> None:
    """Fichier temporaire puis replace, avec relances si OneDrive tient le fichier ouvert."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(attempts):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.5 * (i + 1))


def _save(index: dict, json_path: Path, subject: Path) -> None:
    index["mis_a_jour"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _write_atomic(json_path, json.dumps(index, ensure_ascii=False, indent=2))
    _write_atomic(subject / INDEX_MD, render_markdown(index, subject))


# --------------------------------------------------------------------------- #
#  API (worker)                                                               #
# --------------------------------------------------------------------------- #
def is_indexed(transcript: Path) -> bool:
    """Le cours figure dans l'index de sa matière et son transcript n'a pas changé depuis."""
    _, json_path, _ = _locations(transcript)
    try:
        entry = json.loads(json_path.read_text(encoding="utf-8"))["cours"][course_key(transcript)]
        return entry.get("sha1") == _sha1(transcript)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def index_course(transcript: Path, meta: RecordingMeta, gcfg: GeminiConfig) -> int:
    """Ajoute ce cours à l'index des thèmes de sa matière (INDEX.md réécrit).

    Renvoie le nombre de thèmes concernés. Lève SummarySkipped (rien à indexer, pas de clé)
    ou SummaryError (échec Gemini, réponse inexploitable, index illisible).
    """
    if not gcfg.api_key.strip():
        raise SummarySkipped("clé Gemini absente")
    text, sha1 = _read(transcript)
    numbered = numbered_lines(text)
    if not numbered or not _has_speech(text):
        raise SummarySkipped("rien à indexer")

    subject, json_path, _ = _locations(transcript)
    key = course_key(transcript)
    user = build_prompt(load_index(json_path, meta.matiere, meta.niveau), meta, numbered)
    log.info("Index des thèmes (%s) : %s — ~%d caractères en entrée", gcfg.model, key, len(user))
    reply, model = generate(gcfg, SYSTEM_PROMPT, user, json_schema=SCHEMA)
    items = parse_reply(reply, model)

    course = {
        "titre": meta.titre,
        "date": meta.date,
        "duree": RecordingMeta.hhmmss(meta.duree_s),
        "transcript": transcript.relative_to(subject).as_posix(),
        "sha1": sha1,
        "modele": model,
        "indexe_le": datetime.now().isoformat(timespec="seconds"),
    }
    with _index_lock():
        index = load_index(json_path, meta.matiere, meta.niveau)  # relu : a pu changer entre-temps
        count = merge_course(index, key, course, items, numbered)
        if not count:
            raise SummaryError(f"aucun passage exploitable dans la réponse de {model}")
        _save(index, json_path, subject)
    log.info("Index des thèmes écrit : %s (%d thème(s) pour ce cours)", subject / INDEX_MD, count)
    return count


# --------------------------------------------------------------------------- #
#  Rattrapage (ligne de commande)                                             #
# --------------------------------------------------------------------------- #
def _meta_for(transcript: Path) -> RecordingMeta:
    folder = transcript.parent
    try:
        return RecordingMeta.from_dict(
            json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, TypeError, KeyError):
        subject = folder.parent.parent
        date, _, titre = folder.name.partition("_")
        return RecordingMeta(
            titre=titre or folder.name, matiere=subject.name, niveau=subject.parent.name, date=date
        )


def discover(cfg: Config, matiere: str | None = None) -> dict[Path, list[Path]]:
    """{dossier de matière : transcripts triés par dossier, donc par date}."""
    root = cfg.courses_root_path()
    found: dict[Path, list[Path]] = {}
    for niveau in scan.discover_levels(root):
        for name in scan.discover_subjects(root, niveau):
            if matiere and _norm(name) != _norm(matiere):
                continue
            subject = root / niveau / name
            recordings = subject / cfg.recordings_subdir
            transcripts = sorted(recordings.glob("*/transcript.md")) if recordings.is_dir() else []
            if transcripts or (recordings / INDEX_JSON).is_file():
                found[subject] = transcripts
    return found


def refresh(subject: Path, recordings_subdir: str) -> None:
    """Retire de l'index les cours disparus et réécrit INDEX.md."""
    json_path = subject / recordings_subdir / INDEX_JSON
    if not json_path.is_file():
        return
    with _index_lock():
        index = load_index(json_path, subject.name, subject.parent.name)
        gone = prune_missing(index, subject)
        if gone:
            log.info("   retirés de l'index (dossier introuvable) : %s", ", ".join(gone))
            _save(index, json_path, subject)
        else:
            _write_atomic(subject / INDEX_MD, render_markdown(index, subject))


def _dry_run(transcript: Path, meta: RecordingMeta, label: str) -> None:
    text, _ = _read(transcript)
    numbered = numbered_lines(text)
    if not numbered or not _has_speech(text):
        raise SummarySkipped("rien à indexer")
    _, json_path, _ = _locations(transcript)
    prompt = build_prompt(load_index(json_path, meta.matiere, meta.niveau), meta, numbered)
    log.info(
        "   %s : à indexer — %d paragraphes (L%d à L%d), ~%d caractères de prompt",
        label, len(numbered), numbered[0][0], numbered[-1][0], len(prompt),
    )


def run(*, matiere: str | None = None, force: bool = False, dry_run: bool = False) -> int:
    cfg = Config.load()
    if not dry_run and not cfg.gemini.api_key.strip():
        log.error("Clé Gemini absente (Réglages > Résumé IA) : rien à faire.")
        return 1
    subjects = discover(cfg, matiere)
    if not subjects:
        log.info("Aucun cours transcrit trouvé sous %s.", cfg.courses_root_path())
        return 0

    failures = 0
    for subject, transcripts in subjects.items():
        log.info("== %s / %s : %d cours", subject.parent.name, subject.name, len(transcripts))
        for transcript in transcripts:
            label = course_key(transcript)
            if not force and is_indexed(transcript):
                log.info("   %s : déjà indexé", label)
                continue
            meta = _meta_for(transcript)
            try:
                if dry_run:
                    _dry_run(transcript, meta, label)
                else:
                    count = index_course(transcript, meta, cfg.gemini)
                    log.info("   %s : %d thème(s)", label, count)
            except SummarySkipped as exc:
                log.info("   %s : ignoré (%s)", label, exc)
            except (SummaryError, OSError) as exc:
                failures += 1
                log.error("   %s : échec — %s", label, exc)
        if not dry_run:
            try:
                refresh(subject, cfg.recordings_subdir)
            except (SummaryError, OSError) as exc:
                failures += 1
                log.error("   INDEX.md non réécrit : %s", exc)
    if failures:
        log.error("%d échec(s) — relancer la commande pour réessayer.", failures)
    return 1 if failures else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Index des thèmes par matière (Gemini)")
    ap.add_argument("--all", action="store_true", help="tous les cours transcrits")
    ap.add_argument("--matiere", help="une seule matière (nom du dossier)")
    ap.add_argument("--force", action="store_true", help="réindexer aussi les cours déjà indexés")
    ap.add_argument("--dry-run", action="store_true", help="lister sans appeler Gemini")
    args = ap.parse_args()
    if not (args.all or args.matiere):
        ap.error("préciser --all ou --matiere NOM")

    # sortie lue en UTF-8 par la fenêtre des Réglages (cp1252 par défaut quand elle est redirigée)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass
    setup("index", console=True)
    sys.exit(run(matiere=args.matiere, force=args.force, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
