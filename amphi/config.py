"""Configuration persistante (config.json à la racine de l'app).

Chargée par l'UI *et* par le worker (processus séparé) : garder ce module sans
dépendance lourde.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .paths import CONFIG_PATH, default_courses_root


@dataclass
class EngineConfig:
    # large-v3-turbo : ~2x plus rapide que large-v3 sur ce CPU, précision identique
    # sur du cours d'ingénierie (mesuré). large-v3 reste sélectionnable dans les Réglages.
    model: str = "large-v3-turbo"
    # compute_type / cpu_threads : renseignés par tools/benchmark.py sur cette machine.
    compute_type: str = "int8"          # int8 | int8_float32 | float32
    cpu_threads: int = 4                # 4 cœurs physiques ; le benchmark tranche
    beam_size: int = 5
    vad_filter: bool = True
    word_timestamps: bool = True
    normalize_speech: bool = True       # remonte la parole (speechnorm ffmpeg) avant Whisper
    benchmarked: bool = False           # True une fois le benchmark passé
    benchmark_notes: str = ""


@dataclass
class GeminiConfig:
    """Résumé IA. L'abonnement Google AI Plus ne fournit PAS de clé : en créer une
    (gratuite) sur https://aistudio.google.com/apikey — palier gratuit, Flash only."""

    api_key: str = ""
    # alias maintenu par Google -> pointe toujours sur le Flash courant le plus stable
    # (gemini-3.8-flash est régulièrement saturé -> 503). Modifiable dans les Réglages.
    model: str = "gemini-flash-latest"
    fallback_models: list = field(
        default_factory=lambda: ["gemini-3.5-flash", "gemini-2.0-flash"]
    )
    enabled: bool = True                # sans clé, reste inactif de toute façon
    auto_after_transcription: bool = True
    index_themes: bool = True           # 2ᵉ requête après le résumé : <matière>/INDEX.md
    thinking_budget: int = 4096         # -1 = dynamique ; 0 = désactivé
    prompt_override: str = ""           # remplace le prompt système par défaut si non vide
    acknowledged_privacy: bool = False  # l'utilisateur a vu l'avertissement "envoi en ligne"

    def is_active(self) -> bool:
        return self.enabled and bool(self.api_key.strip())


@dataclass
class Config:
    # --- rangement -------------------------------------------------------------
    courses_root: str = ""
    niveau: str = ""                              # ex. "B2"
    matieres: list[str] = field(default_factory=list)
    matiere_prompts: dict[str, str] = field(default_factory=dict)  # initial_prompt Whisper
    recordings_subdir: str = "Enregistrements"    # sous <niveau>/<matière>/

    # --- audio ---------------------------------------------------------------
    mic_name_hint: str = "microphones numériques"
    mic_device_id: int | None = None             # override explicite (sinon: heuristique)
    mic_gain_db: float = 0.0                     # amplification numérique à la capture (0..24)
    keep_audio_format: str = "flac"              # flac | wav | opus | none
    sample_rate: int = 16000

    # --- comportement ------------------------------------------------------
    ac_only: bool = True                          # transcription seulement sur secteur
    prevent_sleep: bool = True                    # veille inhibée pendant l'enregistrement
    transcribe_while_recording: bool = True       # laisse tourner le worker pendant la capture
    default_langue: str = "fr"

    # --- fenêtre -----------------------------------------------------------
    window_geometry_b64: str = ""                # QByteArray.toBase64 (restaure taille/pos)
    window_state_b64: str = ""                   # QMainWindow.saveState (splitter, etc.)
    splitter_state_b64: str = ""
    always_on_top: bool = False                  # fenêtre Windows classique par défaut
    snap_screenpad_on_launch: bool = True        # ancrer sur le ScreenPad à chaque lancement
    screenpad_side: str = "right"                # "right" | "left"

    # --- onboarding ------------------------------------------------------
    onboarding_done: bool = False                # masque les bandeaux de première config

    # --- sous-sections ---------------------------------------------------
    engine: EngineConfig = field(default_factory=EngineConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)

    # ------------------------------------------------------------------------
    def courses_root_path(self) -> Path:
        return Path(self.courses_root) if self.courses_root else default_courses_root()

    def matiere_prompt(self, matiere: str) -> str:
        return (self.matiere_prompts.get(matiere) or "").strip()

    def target_dir_for(
        self, matiere: str, date: str, titre: str, niveau: str | None = None
    ) -> Path:
        from .paths import sanitize_component

        niveau = self.niveau if niveau is None else niveau
        return (
            self.courses_root_path()
            / sanitize_component(niveau, fallback="Divers")
            / sanitize_component(matiere, fallback="Divers")
            / self.recordings_subdir
            / sanitize_component(f"{date}_{titre}")
        )

    # --- persistance -------------------------------------------------------
    def save(self, path: Path | None = None) -> None:
        """Écrit la config. Sans `path`, réécrit le fichier d'où elle a été chargée."""
        path = path or getattr(self, "_source_path", None) or CONFIG_PATH
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        self._source_path = path

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = Path(path) if path else CONFIG_PATH
        if not path.exists():
            cfg = cls()
            cfg.courses_root = str(default_courses_root())
            cfg._source_path = path
            return cfg
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        nested = {"engine": EngineConfig, "gemini": GeminiConfig}
        for key, sub_type in nested.items():
            if isinstance(raw.get(key), dict):
                raw[key] = _filtered(sub_type, raw[key])
        cfg = cls(**_filter_keys(cls, raw))
        if not cfg.courses_root:
            cfg.courses_root = str(default_courses_root())
        cfg._source_path = path
        return cfg


def _filter_keys(dc_type: type, data: dict[str, Any]) -> dict[str, Any]:
    allowed = {f.name for f in fields(dc_type)}
    return {k: v for k, v in data.items() if k in allowed}


def _filtered(dc_type: type, data: dict[str, Any]) -> Any:
    return dc_type(**_filter_keys(dc_type, data))
