# Documentation d'Amphi

Amphi enregistre les cours, les transcrit localement (Faster-Whisper, CPU), en fait un
résumé et un index des thèmes (Gemini), et relie Moodle IPSA à l'agenda Apple et au
dossier des cours. Le [README](../README.md) à la racine donne la prise en main rapide ;
ce dossier détaille chaque partie.

| Document | Contenu |
|---|---|
| [Installation](installation.md) | prérequis, `install.ps1`, premier lancement, mise à jour |
| [Utilisation](utilisation.md) | fenêtre, enregistrement, marqueurs, file d'attente, raccourcis |
| [Transcription](transcription.md) | worker, cycle de vie d'un enregistrement, batterie, reprise, vocabulaire |
| [Résumé et index des thèmes](resume-et-index.md) | Gemini, modèles de repli, `resume.md`, `INDEX.md` |
| [Moodle](moodle.md) | devoirs → agenda iCloud, documents → dossier des cours, `/maj-moodle`, correspondance cours ↔ dossier |
| [Configuration](configuration.md) | référence de `config.json`, fichiers locaux, secrets |
| [Architecture](architecture.md) | modules, processus, base SQLite, fichiers drapeaux |
| [Dépannage](depannage.md) | messages d'erreur courants et solutions |

## En une image

```
 micro ──► Recorder (UI) ──► .staging\<id>\capture.wav
                                   │  validation (matière, titre, date, langue)
                                   ▼
                         amphi.db  (file d'attente SQLite)
                                   │
                worker (processus séparé, priorité basse, sur secteur)
                                   │  faster-whisper ► segments.jsonl (checkpoint)
                                   ▼
   OneDrive\Cours\<Niveau>\<Matière>\Enregistrements\<date>_<titre>\
        audio.flac · transcript.md · meta.json · resume.md (Gemini)
   OneDrive\Cours\<Niveau>\<Matière>\INDEX.md   (index des thèmes, Gemini)

 Moodle IPSA ──► devoirs ──► calendrier iCloud « Moodle IPSA » (CalDAV)
             └─► documents ──► /maj-moodle (Claude Code) ──► <Matière>\Cours, TP\TPn…
```
