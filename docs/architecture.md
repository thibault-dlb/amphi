# Architecture

Python 3.11, interface Qt (PySide6), Windows uniquement (API `kernel32`, Gestionnaire
d'identifiants, ScreenPad).

## Arborescence

```
amphi/
  app.py            point d'entrée : instance unique (QLocalServer), AppUserModelID, fenêtre
  config.py         dataclasses de config.json (sans dépendance lourde : lu aussi par le worker)
  models.py         Status (cycle de vie), Marker, RecordingMeta
  paths.py          emplacements (APP_DIR, base, staging, drapeaux), nommage des dossiers
  power.py          secteur / batterie, blocage de la veille (ctypes)
  logging_setup.py  journaux par processus, rotation
  audio/
    recorder.py     capture PortAudio : pause sans coupure, marqueurs, sidecar anti-crash
    devices.py      liste et choix du micro
    winmic.py       état muet / niveau du micro Windows (COM)
    encode.py       ffmpeg : FLAC/Opus, réparation WAV, découpe, normalisation
  transcribe/
    worker.py       processus de transcription (file, reprise, pool des résumés)
    engine.py       enveloppe faster-whisper (chargement, annulation, segments)
    markdown.py     transcript.md (en-tête YAML, paragraphes, marqueurs)
    summarize.py    appels Gemini (repli, relances, délai), resume.md
    themes.py       index des thèmes : prompt, fusion, INDEX.md, CLI de rattrapage
  library/
    store.py        base SQLite partagée (mode WAL)
    scan.py         découverte des niveaux et matières dans OneDrive\Cours
  moodle/
    client.py       API REST Moodle (lecture), nom de matière, sections Paris, relances réseau
    model.py        Devoir, EventSpec, EventInfo
    sync.py         synchro devoirs → calendrier (logique pure + run_sync), rapport
    icloud.py       calendrier iCloud par CalDAV
    secrets.py      identifiants Apple (keyring)
    fichiers.py     documents Moodle ↔ dossier local, registre .moodle_sync.json
    claude_launch.py  console Claude Code pour /maj-moodle
    __main__.py     CLI : python -m amphi.moodle sync
  ui/               fenêtre principale, tuiles (Moodle, enregistrement, file), dialogues
.claude/skills/maj-moodle/   skill Claude Code (SKILL.md + driver.py)
tools/
  benchmark.py      mesure le débit réel, écrit compute_type / cpu_threads
  make_icon.py      génère l'icône
install.ps1         installation complète
run.pyw             lanceur sans console
```

## Processus

| Processus | Lancement | Rôle |
|---|---|---|
| UI | `run.pyw` / `python -m amphi` | fenêtre, capture audio, synchro Moodle (thread), lance le worker |
| Worker | `python -m amphi.transcribe.worker` | transcription (thread principal, CPU), résumés et index (pool de threads, réseau) |
| Claude Code | bouton 📥 Récupérer | `/maj-moodle` dans une console PowerShell |

L'UI et le worker communiquent uniquement par la **base SQLite** et des **fichiers
drapeaux** ; aucun socket entre eux.

### Fichiers drapeaux

| Fichier | Posé par | Effet |
|---|---|---|
| `.worker.lock` | worker | un seul worker ; contient son PID, retiré s'il est périmé |
| `.worker.stop` | UI | le worker s'arrête proprement |
| `.recording` | UI | une capture est en cours (le worker s'efface si `transcribe_while_recording` est faux) |
| `.force_battery` | UI | transcrire malgré la batterie ; retiré quand la file est vide |
| `.index.lock` | worker / CLI | écriture de l'index des thèmes en cours |

## Base SQLite (`amphi.db`)

Une table, `recordings`, en mode WAL (UI et worker y accèdent en même temps,
`busy_timeout` de 30 s).

| Colonnes | Contenu |
|---|---|
| `id`, `created_at`, `updated_at`, `status` | identité et état (voir [transcription.md](transcription.md)) |
| `titre`, `matiere`, `niveau`, `date`, `langue`, `duree_s`, `sample_rate`, `markers_json` | métadonnées saisies et mesurées |
| `staging_dir`, `target_dir`, `audio_path`, `transcript_path`, `resume_path` | chemins de travail et livrables |
| `progress`, `stage`, `checkpoint_s`, `error` | avancement affiché, point de reprise, dernière erreur |
| `engine_json` | réglages du moteur au moment de la validation |

Ordre de service du worker : transcription entamée → en pause batterie → nouvelle
transcription → résumé ou index entamé → résumé à faire ; à égalité, le plus ancien d'abord.

## Parcours d'un enregistrement

1. `Recorder` écrit `.staging\rec_<…>\capture.wav` depuis un thread dédié (jamais dans le
   callback audio) et réécrit `recording.json` toutes les 2 s.
2. À la validation, une ligne `queued` est ajoutée dans `amphi.db` ; l'UI lance le worker.
3. Le worker transcrit (`segments.jsonl` après chaque segment), écrit `transcript.md`, puis
   **livre** : audio archivé, `transcript.md` et `meta.json` dans le dossier de la matière
   (statut `transcribed`).
4. Le pool de résumés appelle Gemini : `resume.md`, puis l'index des thèmes. Statut `done`,
   le dossier de staging est supprimé (avec relances si un fichier est encore verrouillé ; sinon
   balayé au prochain démarrage).

## Dépendances

| Paquet | Usage |
|---|---|
| `faster-whisper`, `av` (<17) | transcription ; PyAV 17+ casse faster-whisper 1.2.1 |
| `PySide6` | interface |
| `sounddevice`, `soundfile`, `numpy` | capture, écriture WAV, niveaux |
| `psutil` | priorité du worker, verrou, benchmark |
| `google-genai` | Gemini (facultatif à l'usage) |
| `httpx` | API Moodle |
| `caldav`, `icalendar` | agenda iCloud |
| `keyring` | identifiants Apple |

Outils externes : `ffmpeg` / `ffprobe` (PATH), `claude` (facultatif).
