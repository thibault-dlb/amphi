# Amphi

Enregistreur de cours + transcription locale (Faster-Whisper) + résumé automatique
(Gemini). Conçu pour un **ASUS ZenBook Duo 14 (UX482EA)** : fenêtre sur la moitié
gauche du ScreenPad Plus, transcription sur CPU (pas de GPU/NPU exploitable sur ce
laptop), rangement dans `OneDrive\Cours\<Niveau>\<Matière>\`.

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

Au lancement, l'app **s'ancre sur la moitié droite du ScreenPad** (vrai snap Windows :
si tu ancres une autre app à gauche, les deux se redimensionnent ensemble par la poignée
centrale). Réglable dans Réglages > Comportement (côté, ou désactiver l'ancrage auto).

| Action | Où |
|---|---|
| Démarrer / arrêter | gros bouton rouge |
| Pause / reprise | bouton **⏸** ou `Ctrl+P` |
| Marqueur « important » | bouton **＋** ou `Ctrl+M` |
| Classer à l'arrêt | dialogue matière / titre / date / langue |
| Suivi transcription | panneau de droite |
| Réglages | menu **Fichier** ou `Ctrl+,` |
| Toujours au-dessus | menu **Affichage** |
| Ré-ancrer sur le ScreenPad | menu **Affichage** ou `Ctrl+Home` |

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

## Architecture

```
amphi/
  audio/       capture (pause/marqueurs/anti-crash), encodage FLAC
  transcribe/  moteur faster-whisper, worker autonome, rendu Markdown, résumé Gemini
  library/     index SQLite, découverte des matières
  ui/          fenêtre compacte ScreenPad, dialogues
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
