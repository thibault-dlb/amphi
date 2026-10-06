# Amphi

Enregistreur de cours + transcription locale (Faster-Whisper) + résumé automatique
(Gemini). Transcription sur CPU, rangement dans `<racine>\<Niveau>\<Matière>\`.

## Modifications de cette branche

Adaptation à un laptop sans ScreenPad (Intel i5-1340P, 16 Go, sans GPU dédié) :

- **Suppression de tout le support ScreenPad Plus** : module `amphi/ui/screens.py`
  supprimé, plus d'ancrage automatique au lancement, plus d'entrée « Ancrer sur le
  ScreenPad » (`Ctrl+Home`) dans le menu Affichage, plus d'options côté/ancrage dans
  Réglages > Comportement, champs `snap_screenpad_on_launch` / `screenpad_side` retirés
  de la config (ignorés s'ils subsistent dans un ancien `config.json`). Sans géométrie
  sauvegardée, la fenêtre s'ouvre centrée sur l'écran principal.
- **Correctif de dépendance** : `faster-whisper==1.2.1` plante avec PyAV 17+
  (`open() got an unexpected keyword argument 'metadata_errors'`) à chaque
  transcription. `requirements.txt` épingle désormais `av>=15,<17`.
- **Réglage moteur pour CPU hybride** (dans `config.json`, local) : le benchmark
  d'origine ne teste que 4 et 8 threads. Mesures sur i5-1340P (4P + 8E cœurs),
  `large-v3-turbo` int8, beam 5, échantillon de 55 s :

  | Threads | RTF |
  |---|---|
  | 4 | 0,63 |
  | **6** | **0,60** |
  | 8 | 0,67 |
  | 12 | 0,91 |

  → `cpu_threads = 6`, ~36 min de calcul pour 1 h de cours. Même nombre de mots
  transcrits dans toutes les configurations.
- Docstrings et README débarrassés des mentions ScreenPad / ZenBook.

## Installation

```powershell
cd C:\Users\thibd\Amphi
powershell -ExecutionPolicy Bypass -File install.ps1
```

Options : `-Quick` (benchmark court), `-SkipBenchmark`, `-SkipModel`.

Le script crée `.venv`, installe les dépendances, télécharge le modèle
(`large-v3-turbo`, ~1,6 Go), mesure la vitesse sur la machine, et pose un raccourci
**Amphi** sur le Bureau et dans le menu Démarrer.

## Premier lancement

1. Ouvre **Réglages** (⚙ dans la barre de titre).
2. **Rangement** : vérifie le dossier des cours, choisis ton **niveau**, clique
   « Scanner les matières ».
3. **Résumé IA** (facultatif) : colle une clé API Gemini créée sur
   <https://aistudio.google.com/apikey>, puis « Tester ».
   ⚠️ L'abonnement **Google AI Plus ne fournit pas** de clé — c'est un produit
   développeur distinct, gratuit pour les modèles Flash.
   Modèle par défaut : `gemini-flash-latest`. En cas de saturation Google (erreur 503),
   l'app bascule seule sur `gemini-3.5-flash` puis `gemini-2.0-flash` ; si tout est
   saturé, le résumé est reporté (bouton « Relancer le résumé » dans la file).

## Usage

Fenêtre Windows normale : redimensionnable, `Win`+flèches (snap), minimisation,
menus **Fichier** / **Affichage**, icône dans la zone de notification.

| Action | Où |
|---|---|
| Démarrer / arrêter | gros bouton rouge |
| Pause / reprise | bouton **⏸** ou `Ctrl+P` |
| Marqueur « important » | bouton **＋** ou `Ctrl+M` |
| Classer à l'arrêt | dialogue matière / titre / date / langue |
| Suivi transcription | panneau de droite |
| Réglages | menu **Fichier** ou `Ctrl+,` |
| Toujours au-dessus | menu **Affichage** |

À l'arrêt, l'enregistrement part en file. La transcription (puis le résumé) se
lance **quand le laptop est sur secteur** ; sur batterie elle se met en pause et
reprend au branchement, sans reperdre le travail déjà fait.

### Vitesse (mesurée sur ce ZenBook, échantillon de français technique)

| Modèle | RTF | 1 h 30 de cours ≈ | Transcription |
|---|---|---|---|
| `large-v3-turbo` (**défaut**, int8/8t) | **0,74** | **~1 h de calcul** | identique à large-v3 sur ce test |
| `large-v3` (int8/4t) | 3,1 | ~4 h 40 de calcul | reprend l'avantage sur audio difficile |

Pas de GPU/NPU exploitable → tout est sur CPU. `large-v3` était le choix initial mais a
été écarté au vu des mesures : sur du cours d'ingénierie il produit le même texte que
turbo pour 4× le temps. Bascule en un clic dans Réglages > Micro & moteur si tu veux le
maximum de robustesse sur un enregistrement bruité. Baisser le *beam size* à 1–2
accélère encore (léger coût en précision).

Résultat dans `OneDrive\Cours\<Niveau>\<Matière>\Enregistrements\<date>_<titre>\` :

```
audio.flac       enregistrement recompressé sans perte
transcript.md    transcription horodatée + marqueurs
resume.md        plan + synthèse + formules (si Gemini configuré)
meta.json        métadonnées
```

### Index des thèmes

Après le résumé, une 2ᵉ requête Gemini met à jour l'index des thèmes de la matière, pensé pour
qu'un agent IA (Claude Code…) retrouve où chaque notion est traitée sans relire les transcripts :

```
<Matière>\INDEX.md                          thèmes → passages (cours, lignes du transcript, nature, note)
<Matière>\Enregistrements\index_themes.json données de l'index (ne pas éditer)
```

Gemini reçoit la liste des thèmes déjà indexés et le transcript du nouveau cours, lignes
numérotées ; il ne renvoie que l'apport de ce cours, que l'app fusionne dans l'index. Les
références des cours précédents ne repassent jamais par le modèle. Désactivable dans
Réglages > Résumé IA. En cas d'échec, bouton « Relancer l'index » dans la file.

Cours transcrits avant l'activation : Réglages > Résumé IA > « Indexer les cours déjà
transcrits… », ou

```powershell
.\.venv\Scripts\python -m amphi.transcribe.themes --all   # --matiere NOM · --force · --dry-run
```

## Architecture

```
amphi/
  audio/       capture (pause/marqueurs/anti-crash), encodage FLAC
  transcribe/  moteur faster-whisper, worker autonome, rendu Markdown, résumé + index des thèmes (Gemini)
  library/     index SQLite, découverte des matières
  ui/          fenêtre compacte, dialogues
tools/
  benchmark.py mesure le débit réel et fixe compute_type / cpu_threads
  make_icon.py génère l'icône
```

Le **worker** (`python -m amphi.transcribe.worker`) est un processus séparé : l'UI
reste fluide, un plantage n'emporte pas un enregistrement en cours, la priorité est
basse. Il écrit un checkpoint (`segments.jsonl`) après chaque segment et sait
reprendre. L'app le relance au besoin ; il s'arrête seul quand la file est vide.

## Journaux

`logs\amphi.log` (UI) et `logs\worker.log` (transcription).

## Relancer un benchmark

Réglages → *Micro & moteur* → « Lancer un benchmark », ou
`.\.venv\Scripts\python tools\benchmark.py`.
