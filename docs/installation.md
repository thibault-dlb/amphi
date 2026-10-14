# Installation

## Prérequis

- **Windows 10/11**. Amphi est pensé pour l'ASUS ZenBook Duo 14 (UX482EA, i7-1165G7) et son
  ScreenPad Plus, mais fonctionne sur tout PC Windows.
- **Python 3.11+** (`py -3.11` ou `python` dans le PATH).
- **ffmpeg / ffprobe** dans le PATH : encodage FLAC/Opus, réparation d'un WAV après crash,
  normalisation de la parole, reprise d'une transcription. Sans ffmpeg, l'audio est archivé
  en WAV et certaines fonctions sont désactivées.
- Facultatif : une **clé API Gemini** (résumés), un **token Moodle** et un **mot de passe
  d'app Apple** (agenda), la commande **`claude`** (Claude Code, bouton 📥 Récupérer).

## Script d'installation

```powershell
cd C:\Users\thibd\Amphi
powershell -ExecutionPolicy Bypass -File install.ps1
```

| Option | Effet |
|---|---|
| `-Quick` | benchmark court (~4 min au lieu de ~13) |
| `-SkipBenchmark` | pas de benchmark (relançable depuis les Réglages) |
| `-SkipModel` | ne pas pré-télécharger le modèle Whisper |

Le script :

1. trouve Python 3.11+ ;
2. crée `.venv` et installe `requirements.txt` ;
3. télécharge le modèle `large-v3-turbo` (~1,6 Go, cache Hugging Face) ;
4. génère l'icône (`tools/make_icon.py`) ;
5. lance `tools/benchmark.py`, qui écrit `compute_type` et `cpu_threads` dans `config.json` ;
6. pose un raccourci **Amphi** sur le Bureau et dans le menu Démarrer (lancement par
   `pythonw`, sans console).

`install.ps1` est en ASCII pur : Windows PowerShell 5.1 lit mal l'UTF-8 sans BOM.

## Lancer Amphi

- Raccourci **Amphi** (Bureau / menu Démarrer), ou
- `.\.venv\Scripts\pythonw run.pyw` (sans console), ou
- `.\.venv\Scripts\python -m amphi` (avec console, pratique pour déboguer).

Une seule instance tourne à la fois : relancer Amphi ramène la fenêtre existante au premier
plan.

## Premier lancement

1. **Réglages** (`Ctrl+,`) → **Rangement** : vérifier le dossier des cours (par défaut
   `OneDrive\Cours`), choisir le **niveau** (`B3`…), cliquer « Scanner les matières ». Les
   matières sont les sous-dossiers de `<dossier des cours>\<niveau>`.
2. **Micro & moteur** : vérifier le micro détecté (par défaut celui dont le nom contient
   « microphones numériques ») et le modèle.
3. **Résumé IA** (facultatif) : coller une clé Gemini créée sur
   <https://aistudio.google.com/apikey>, puis « Tester ». L'abonnement Google AI Plus ne
   fournit **pas** de clé : c'est un produit développeur distinct, gratuit pour les modèles
   Flash.
4. **Moodle** (facultatif) : voir [moodle.md](moodle.md) — token Moodle puis agenda Apple.

## Mettre à jour

```powershell
cd C:\Users\thibd\Amphi
git pull
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Puis quitter Amphi depuis l'icône de la zone de notification et le relancer. `config.json`,
`amphi.db` et les secrets ne sont pas suivis par git : une mise à jour ne les touche pas.

## Relancer le benchmark

Réglages → *Micro & moteur* → « Lancer un benchmark », ou :

```powershell
.\.venv\Scripts\python tools\benchmark.py            # complet
.\.venv\Scripts\python tools\benchmark.py --quick    # rapide
.\.venv\Scripts\python tools\benchmark.py extrait.wav  # sur un vrai enregistrement
```
