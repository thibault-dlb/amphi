# Configuration

Toute la configuration est dans **`config.json`**, à la racine d'Amphi (ignoré par git). Il
est écrit par les Réglages ; on peut l'éditer à la main, Amphi fermé. Une clé inconnue est
ignorée ; une clé absente prend sa valeur par défaut (`amphi/config.py`).

## Rangement

| Clé | Défaut | Rôle |
|---|---|---|
| `courses_root` | `OneDrive\Cours` (détecté) | racine des cours |
| `niveau` | — | sous-dossier du niveau (`B3`…) |
| `matieres` | — | liste des matières proposées (scan + ajouts manuels) |
| `matiere_prompts` | `{}` | prompt initial Whisper par matière |
| `recordings_subdir` | `Enregistrements` | sous-dossier des enregistrements dans chaque matière |

Chemin d'un enregistrement :
`<courses_root>\<niveau>\<matière>\<recordings_subdir>\<date>_<titre>\`. Un niveau ou une
matière vide donne `Divers`.

## Audio

| Clé | Défaut | Rôle |
|---|---|---|
| `mic_name_hint` | `microphones numériques` | sous-chaîne pour choisir le micro |
| `mic_device_id` | `null` | périphérique imposé (sinon heuristique) |
| `mic_gain_db` | `0` | gain numérique à la capture (0 à 24 dB) |
| `keep_audio_format` | `flac` | `flac`, `wav`, `opus` ou `none` |
| `sample_rate` | `16000` | fréquence enregistrée dans les métadonnées (la capture se fait à la fréquence native du micro) |

## Comportement

| Clé | Défaut | Rôle |
|---|---|---|
| `ac_only` | `true` | transcrire et résumer seulement sur secteur |
| `prevent_sleep` | `true` | bloquer la veille pendant un enregistrement |
| `transcribe_while_recording` | `true` | le worker continue pendant une capture |
| `default_langue` | `fr` | langue proposée à la validation |

## Fenêtre

| Clé | Défaut | Rôle |
|---|---|---|
| `snap_screenpad_on_launch` | `true` | ancrer sur le ScreenPad au lancement |
| `screenpad_side` | `full` | `full`, `right` ou `left` |
| `always_on_top` | `false` | toujours au-dessus |
| `window_geometry_b64`, `window_state_b64`, `splitter_state_b64` | — | état de la fenêtre (géré par Amphi) |
| `onboarding_done` | `false` | masque les bandeaux de première configuration |

## `engine` — transcription

| Clé | Défaut | Rôle |
|---|---|---|
| `model` | `large-v3-turbo` | modèle faster-whisper |
| `compute_type` | `int8` | `int8`, `int8_float32` ou `float32` (benchmark) |
| `cpu_threads` | `4` | threads CPU (benchmark) |
| `beam_size` | `5` | largeur du faisceau |
| `vad_filter` | `true` | filtre de silences |
| `word_timestamps` | `true` | horodatage par mot |
| `normalize_speech` | `true` | normalisation de la parole avant Whisper |
| `benchmarked`, `benchmark_notes` | — | résultat du dernier benchmark |

## `gemini` — résumé et index

| Clé | Défaut | Rôle |
|---|---|---|
| `api_key` | `""` | clé Google AI Studio (en clair dans `config.json`) |
| `model` | `gemini-flash-latest` | modèle principal |
| `fallback_models` | liste | modèles de repli, dans l'ordre |
| `enabled` | `true` | active le résumé (inactif sans clé de toute façon) |
| `auto_after_transcription` | `true` | lancer le résumé seul après la transcription |
| `index_themes` | `true` | 2ᵉ requête : `INDEX.md` de la matière |
| `thinking_budget` | `4096` | `-1` dynamique, `0` désactivé |
| `prompt_override` | `""` | remplace le prompt système du résumé |
| `acknowledged_privacy` | `false` | avertissement « envoi en ligne » vu |

## `moodle`

| Clé | Défaut | Rôle |
|---|---|---|
| `enabled` | `false` | `true` une fois iCloud configuré |
| `base_url` | `https://moodle.ipsa.fr` | instance Moodle |
| `calendar_name` | `Moodle IPSA` | calendrier iCloud cible (créé s'il manque) |
| `sync_on_startup` | `true` | synchro silencieuse au lancement |
| `last_sync` | — | horodatage de la dernière synchro réussie |
| `docs_signales` | `[]` | plus utilisé (servait à ne pas rouvrir le rapport de synchro) |

## Fichiers locaux d'Amphi

Tous à la racine d'Amphi, ignorés par git :

| Fichier | Rôle |
|---|---|
| `config.json` | configuration (contient la clé Gemini) |
| `amphi.db` (+ `-wal`, `-shm`) | file d'attente et historique des enregistrements (SQLite) |
| `.staging\` | captures en cours et travail du worker, nettoyé après livraison |
| `logs\` | `amphi.log` (UI), `worker.log` (transcription), `moodle.log` (CLI) ; rotation 2 Mo × 5 |
| `.moodle_token`, `.moodle_ical_url` | secrets Moodle |
| `moodle_last.json` | dernier tableau de synchro Moodle |
| `.worker.lock`, `.worker.stop`, `.recording`, `.force_battery`, `.index.lock` | drapeaux entre processus (voir [architecture.md](architecture.md)) |
| `amphi\assets\` | icône générée |

À sauvegarder si on change de PC : `config.json`, `amphi.db`, `.moodle_token`. Les livrables
(audio, transcripts, résumés, index) sont dans OneDrive.
