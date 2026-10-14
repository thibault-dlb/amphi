# Transcription

## Le worker

La transcription tourne dans un **processus séparé** :
`python -m amphi.transcribe.worker`. Ainsi l'interface reste fluide, un plantage de la
transcription n'emporte pas un enregistrement en cours, et le calcul tourne en **priorité
basse** (CPU et disque).

- L'app lance le worker dès que la file contient du travail ; il s'arrête seul après 3 min
  sans travail.
- Un seul worker à la fois (verrou `.worker.lock`, contenant son PID ; un verrou périmé est
  retiré).
- Les **transcriptions passent avant les résumés**. Les appels Gemini (résumé, index)
  tournent en parallèle dans un pool de threads, pour ne pas retarder la transcription
  suivante.

Options de ligne de commande : `--once` (traite la file puis sort), `--idle-exit SECONDES`.

## Cycle de vie d'un enregistrement

| Statut (`amphi.db`) | Libellé | Signification |
|---|---|---|
| `queued` | en attente | validé, attend le worker |
| `transcribing` | transcription | faster-whisper en cours |
| `paused_no_ac` | en pause (batterie) | reprendra au branchement |
| `paused_user` | en pause | attend un clic « Reprendre » |
| `transcribed` | transcript prêt | livré dans le dossier de la matière, résumé à faire |
| `summarizing` | résumé… | requête Gemini (résumé) |
| `indexing` | index des thèmes… | requête Gemini (index) |
| `done` | terminé | tout est livré, dossier de staging supprimé |
| `transcribe_failed` | échec transcription | bouton « Réessayer » |
| `summary_failed` | résumé échoué | transcript livré ; bouton « Relancer le résumé » |
| `index_failed` | index échoué | transcript + résumé livrés ; bouton « Relancer l'index » |

## Batterie et enregistrement en cours

- **`ac_only`** (défaut : oui) : sur batterie, la transcription et les appels Gemini se
  mettent en pause et reprennent au branchement, sans perdre le travail fait. Le bouton
  « ⚡ Transcrire maintenant (batterie) » crée le drapeau `.force_battery`, consommé quand la file est
  vide.
- **`transcribe_while_recording`** (défaut : oui) : le worker continue pendant un
  enregistrement. Désactivé, il s'efface tant que `.recording` existe, pour laisser tout le CPU
  à la capture.

## Reprise après interruption

Chaque segment transcrit est ajouté à `.staging\<id>\segments.jsonl` (écriture + `fsync`).
Après une interruption (batterie, pause, crash, arrêt du PC) :

- si plus de 15 s étaient faites, ffmpeg coupe l'audio au dernier segment (`resume.wav`) et la
  transcription repart de là ; les 400 derniers caractères servent de contexte à Whisper ;
- sinon, ou si la découpe échoue, la transcription repart du début avec un checkpoint vierge
  (pas de segments en double).

Une ligne JSONL tronquée par un crash est ignorée et refaite.

## Moteur

faster-whisper (CTranslate2) sur CPU. Réglages dans **Réglages → Micro & moteur** :

| Réglage | Défaut | Note |
|---|---|---|
| `model` | `large-v3-turbo` | `large-v3` : même texte sur du cours d'ingénierie, 4× plus lent ; plus robuste sur audio bruité |
| `compute_type` | `int8` | fixé par le benchmark |
| `cpu_threads` | `4` | fixé par le benchmark |
| `beam_size` | `5` | 1–2 accélère, léger coût en précision |
| `vad_filter` | oui | saute les silences |
| `normalize_speech` | oui | remonte la parole (filtre `speechnorm` de ffmpeg) avant Whisper, sur une copie : l'archive reste l'original |

Vitesse mesurée sur le ZenBook (RTF = temps de calcul / durée audio) :

| Modèle | RTF | 1 h 30 de cours ≈ |
|---|---|---|
| `large-v3-turbo` (int8, 8 threads) | 0,74 | ~1 h de calcul |
| `large-v3` (int8, 4 threads) | 3,1 | ~4 h 40 de calcul |

Le décodage est calibré pour du cours technique (`best_of=5`, température en cascade,
`hallucination_silence_threshold=2.0` contre le texte inventé sur les silences d'amphi).

## Aider Whisper sur le vocabulaire d'une matière

Deux leviers, par matière :

- **Prompt initial** (`matiere_prompts` dans `config.json`, éditable dans les Réglages) :
  texte d'amorce envoyé à Whisper.
- **Vocabulaire** : fichier
  `<Matière>\Enregistrements\vocabulaire.txt`. Seule la **première ligne non vide qui ne
  commence pas par `#`** est envoyée comme `hotwords` ; les lignes `#` servent de commentaires
  ou de réserve de termes. Quand un vocabulaire est fourni,
  `condition_on_previous_text` est désactivé pour lui laisser la place.

  ```
  # Vocabulaire Machine learning — termes du moment sur la ligne suivante
  régression logistique, k-means, perceptron, rétropropagation, sur-apprentissage
  # réserve : SVM, noyau gaussien, validation croisée
  ```

Les termes utilisés sont recopiés dans `meta.json` (`engine.hotwords`).

## Audio archivé

`keep_audio_format` : `flac` (défaut, sans perte), `wav`, `opus` (24 kb/s) ou `none`. Si
l'encodage échoue (ffmpeg absent), Amphi garde le WAV.
