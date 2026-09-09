"""Résumé de cours via Gemini (google-genai). Optionnel : sans clé API, non appelé.

Rappel : l'abonnement Google AI Plus ne fournit PAS de clé. En créer une, gratuite,
sur https://aistudio.google.com/apikey (palier gratuit → modèles Flash).
"""

from __future__ import annotations

import logging
import time

from ..config import GeminiConfig
from ..models import RecordingMeta

log = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """\
Tu es un assistant qui produit la fiche de révision d'un cours d'ingénierie à partir de \
sa transcription automatique (donc imparfaite).

Consignes :
- Réponds en français, en Markdown.
- Structure EXACTEMENT en trois sections, dans cet ordre :
  ## Plan du cours
  ## Synthèse
  ## Définitions, théorèmes et formules clés
- « Plan du cours » : plan hiérarchique (titres, sous-titres) reflétant la progression réelle.
- « Synthèse » : prose dense, section par section, qui explique les idées et leur enchaînement.
  Vise l'essentiel utile en révision, pas un résumé télégraphique.
- « Définitions, théorèmes et formules clés » : liste à puces. Formules en LaTeX ($...$ inline,
  $$...$$ en bloc). Une puce par notion.
- N'invente rien. Ne comble aucun trou. Si un passage est manifestement mal transcrit ou
  incompréhensible, signale-le : [passage incertain : "..."].
- Si le prof insiste sur un point (« retenez », « ça tombe à l'exam », « important »), marque-le
  d'un 🔑 en début de puce ou de phrase.
- Reste collé au contenu du cours ; pas de généralités de culture générale.
"""


class SummaryError(RuntimeError):
    pass


class SummarySkipped(RuntimeError):
    """La dépendance google-genai n'est pas installée."""


def strip_front_matter(md: str) -> str:
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end != -1:
            nl = md.find("\n", end + 1)
            return md[nl + 1 :].lstrip() if nl != -1 else ""
    return md


def _client(api_key: str):
    try:
        from google import genai  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise SummarySkipped(str(exc)) from exc
    return genai


def list_models(api_key: str) -> list[str]:
    """Modèles de génération de texte visibles par cette clé (pour les réglages)."""
    genai = _client(api_key)
    client = genai.Client(api_key=api_key)
    names: list[str] = []
    try:
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None) or getattr(
                m, "supported_generation_methods", []
            )
            if actions and "generateContent" not in actions:
                continue
            name = (getattr(m, "name", "") or "").split("/")[-1]
            if name.startswith("gemini"):
                names.append(name)
    except Exception as exc:  # noqa: BLE001
        log.warning("Liste des modèles Gemini indisponible : %s", exc)
    return sorted(set(names), reverse=True)


def verify_key(gcfg: GeminiConfig) -> tuple[bool, str]:
    try:
        genai = _client(gcfg.api_key)
        client = genai.Client(api_key=gcfg.api_key)
    except SummarySkipped:
        return (False, "Paquet google-genai absent")
    except Exception as exc:  # noqa: BLE001
        return (False, str(exc)[:300])

    tried = []
    for model in [gcfg.model, *gcfg.fallback_models]:
        try:
            resp = client.models.generate_content(model=model, contents="Réponds OK.")
            text, reason = _extract_text(resp)
            if text:
                extra = f" (via {model})" if model != gcfg.model else ""
                return (True, "Clé valide" + extra)
            tried.append(f"{model}: vide/{reason}")
        except Exception as exc:  # noqa: BLE001
            s = str(exc)
            if _is_overloaded(exc):
                tried.append(f"{model}: saturé (503)")
                continue
            if "403" in s and "SERVICE_DISABLED" in s:
                return (False, "API Gemini désactivée sur ce projet Google Cloud "
                               "— active-la ou recrée une clé sur aistudio.google.com/apikey")
            if "API_KEY_INVALID" in s or "API key not valid" in s:
                return (False, "Clé API invalide")
            return (False, s[:250])
    return (False, "Tous les modèles saturés ou indisponibles — " + " ; ".join(tried))


def _is_bad_kwarg(exc: Exception) -> bool:
    s = str(exc).lower()
    return "unexpected keyword" in s or "got an unexpected" in s or "thinking" in s


def _is_overloaded(exc: Exception) -> bool:
    s = str(exc)
    return "503" in s or "UNAVAILABLE" in s or "overloaded" in s.lower() or "429" in s


def _is_missing_model(exc: Exception) -> bool:
    s = str(exc)
    return "404" in s or "NOT_FOUND" in s or "not found" in s.lower()


def _extract_text(resp) -> tuple[str, str]:
    """Renvoie (texte, raison). Gère les modèles à raisonnement dont .text peut être vide."""
    text = (getattr(resp, "text", None) or "").strip()
    if text:
        return text, ""
    reason = ""
    for cand in getattr(resp, "candidates", None) or []:
        reason = str(getattr(cand, "finish_reason", "") or reason)
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "thought", False):
                continue
            piece = getattr(part, "text", None)
            if piece:
                text += piece
    fb = getattr(resp, "prompt_feedback", None)
    if fb and not reason:
        reason = str(fb)
    return text.strip(), reason


def _one_call(client, types, model: str, system: str, user: str, thinking: int) -> str:
    cfg_kwargs: dict = dict(
        system_instruction=system, temperature=0.3, max_output_tokens=32000
    )
    if thinking != 0:
        try:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking)
        except Exception:  # noqa: BLE001
            pass
    try:
        resp = client.models.generate_content(
            model=model, contents=user,
            config=types.GenerateContentConfig(**cfg_kwargs),
        )
    except Exception as exc:  # noqa: BLE001
        if "thinking_config" in cfg_kwargs and _is_bad_kwarg(exc):
            cfg_kwargs.pop("thinking_config")
            resp = client.models.generate_content(
                model=model, contents=user,
                config=types.GenerateContentConfig(**cfg_kwargs),
            )
        else:
            raise
    text, reason = _extract_text(resp)
    if text:
        return text
    if reason == "MAX_TOKENS" and thinking != 0:
        # le raisonnement a mangé tout le budget : on réessaie sans raisonnement
        return _one_call(client, types, model, system, user, thinking=0)
    raise SummaryError(f"réponse vide de {model} (finish_reason={reason or 'inconnu'})")


def _generate(gcfg: GeminiConfig, system: str, user: str) -> tuple[str, str]:
    """Renvoie (résumé, modèle réellement utilisé)."""
    genai = _client(gcfg.api_key)
    from google.genai import types  # noqa: PLC0415

    client = genai.Client(api_key=gcfg.api_key)
    models = [gcfg.model, *[m for m in gcfg.fallback_models if m != gcfg.model]]

    last_exc: Exception | None = None
    for mi, model in enumerate(models):
        for attempt in range(4):
            try:
                out = _one_call(client, types, model, system, user, gcfg.thinking_budget)
                if mi > 0:
                    log.info("Résumé via le modèle de repli %s", model)
                return out, model
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_missing_model(exc):
                    log.info("%s indisponible pour cette clé — modèle suivant", model)
                    break
                if _is_overloaded(exc) and attempt < 3:
                    wait = 5 * (2**attempt)
                    log.warning("%s saturé (tentative %d/4) — attente %ds", model, attempt + 1, wait)
                    time.sleep(wait)
                    continue
                if _is_overloaded(exc):
                    log.warning("%s toujours saturé — modèle suivant", model)
                    break
                raise SummaryError(f"{type(exc).__name__}: {exc}") from exc
    raise SummaryError(str(last_exc) if last_exc else "échec inconnu")


def _q(value: str) -> str:
    """Valeur YAML entre guillemets, échappée."""
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _assemble(meta: RecordingMeta, body: str, model: str) -> str:
    warn = "Résumé généré par IA à partir d'une transcription automatique — à vérifier."
    fm = [
        "---",
        "titre: " + _q(meta.titre),
        "matiere: " + _q(meta.matiere),
        "niveau: " + _q(meta.niveau),
        "date: " + meta.date,
        "duree: " + RecordingMeta.hhmmss(meta.duree_s),
        "modele_resume: " + model,
        "avertissement: " + _q(warn),
        "---",
        "",
        f"# {meta.matiere} — {meta.titre} · Résumé",
        "",
        "> ⚠️ Généré automatiquement par Gemini à partir du transcript. "
        "Vérifie les formules et les définitions avant de réviser dessus.",
        "",
        body.strip(),
        "",
    ]
    return "\n".join(fm)


def summarize(transcript_md: str, meta: RecordingMeta, gcfg: GeminiConfig) -> str:
    """Renvoie le contenu de resume.md. Lève SummaryError / SummarySkipped en cas d'échec."""
    if not gcfg.is_active():
        raise SummarySkipped("Gemini non configuré")

    body_in = strip_front_matter(transcript_md)
    system = (gcfg.prompt_override or "").strip() or DEFAULT_SYSTEM_PROMPT
    user = (
        f"Cours : {meta.matiere} — {meta.titre}\n"
        f"Niveau : {meta.niveau}\nDate : {meta.date}\n"
        f"Durée : {RecordingMeta.hhmmss(meta.duree_s)}\nLangue : {meta.langue}\n\n"
        f"Transcription automatique à synthétiser :\n\n{body_in}"
    )
    log.info("Résumé Gemini (%s) : ~%d caractères en entrée", gcfg.model, len(user))
    text, model_used = _generate(gcfg, system, user)
    return _assemble(meta, text, model_used)
