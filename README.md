# Amphi

Enregistreur de cours + transcription locale (Faster-Whisper) + résumé automatique
(Gemini). Conçu pour un **ASUS ZenBook Duo 14 (UX482EA)** : fenêtre en plein écran
sur le ScreenPad Plus, transcription sur CPU (pas de GPU/NPU exploitable sur ce
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
   l'app bascule seule sur `gemini-3.5-flash` puis `gemini-3.6-flash` ; si tout est
   saturé, le résumé est reporté (bouton « Relancer le résumé » dans la file).

## Usage

Fenêtre Windows normale : redimensionnable, `Win`+flèches (snap), minimisation,
menus **Fichier** / **Affichage**, icône dans la zone de notification.

Au lancement, l'app **s'ouvre en plein écran sur le ScreenPad** (fenêtre maximisée : les
trois tuiles Moodle / Enregistrement / File d'attente côte à côte). Réglable dans Réglages >
Comportement : plein écran, moitié droite ou moitié gauche (vrai snap Windows : avec une
autre app ancrée sur l'autre moitié, les deux se redimensionnent ensemble), ou désactiver
l'ancrage auto.

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
| Synchroniser les devoirs Moodle → agenda Apple | bouton **⟳ Synchroniser Moodle** (tuile « MOODLE », à gauche) ou `Ctrl+D` |

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

## Devoirs Moodle → agenda Apple

Amphi copie les devoirs Moodle (IPSA, **campus Paris uniquement**) dans un calendrier iCloud
« Moodle IPSA », visible sur l'iPhone, avec 3 rappels (J-3, J-1, 3 h avant). Un devoir rendu
reste dans l'agenda, marqué ✅, sans rappel. Deux connexions à faire une seule fois : Moodle,
puis Apple.

### Étape A — Connexion à Moodle (token)

Le token se récupère par le navigateur, car la connexion IPSA passe par le compte Microsoft
(Office 365). Les étapes valent pour Opera, Chrome ou Edge.

1. Ouvrir le navigateur et se connecter normalement sur **https://moodle.ipsa.fr** (bouton
   Office 365, compte Microsoft IPSA). Attendre d'être sur le tableau de bord.
2. Dans un **nouvel onglet** du même navigateur, ouvrir cette adresse (une seule ligne) :

   ```
   https://moodle.ipsa.fr/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodlemobile&confirmed=1
   ```

   Une page Moodle « Votre enregistrement a été confirmé » s'affiche, avec un bouton pour
   lancer l'application. **Ne pas cliquer dessus.**
3. Sur cette page, faire **Ctrl+U** : le code source s'ouvre dans un nouvel onglet.
4. Faire **Ctrl+F** et chercher `token=`. On tombe sur `moodlemobile://token=…`.
5. Sélectionner à la souris le texte de `moodlemobile://token=` jusqu'au guillemet `"` qui suit
   (déborder un peu n'est pas grave), puis **Ctrl+C**. Ne rien copier d'autre ensuite.
6. Ouvrir **PowerShell** (touche Windows, taper `powershell`, Entrée), coller ce bloc puis
   Entrée. Il décode le presse-papiers et enregistre le token **sans l'afficher** :

   ```powershell
   $raw = (Get-Clipboard) -join ''
   $b64 = ($raw -replace '^.*token=', '') -replace '["''<>\s].*$', ''
   $parts = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)) -split ':::'
   $parts[1] | Set-Content -NoNewline "$env:USERPROFILE\Amphi\.moodle_token"
   "Token enregistré ($($parts[1].Length) caractères)"
   ```

   Résultat attendu : **« Token enregistré (32 caractères) »**. Si « 0 caractères » : la copie
   de l'étape 5 n'a pas été prise, la refaire.
7. Vérifier (lecture seule, rien n'est écrit dans iCloud) :

   ```powershell
   cd $env:USERPROFILE\Amphi
   .\.venv\Scripts\python -m amphi.moodle sync --no-icloud
   ```

   La liste des devoirs Paris de l'année doit s'afficher.

Le token est stocké dans `.moodle_token` (ignoré par git). Il donne accès au compte Moodle :
ne jamais le partager. S'il expire, Amphi affiche « token Moodle expiré » : refaire les
étapes 1 à 6. Pour le révoquer : Moodle → Préférences → Clés de sécurité.

### Étape B — Connexion à Apple (iCloud)

Amphi écrit dans iCloud par CalDAV avec un **mot de passe d'application** Apple (le mot de
passe habituel du compte ne fonctionne pas).

1. Sur le PC, ouvrir **https://account.apple.com** et se connecter avec l'identifiant Apple
   (valider le code reçu sur l'iPhone).
2. Aller dans **Connexion et sécurité** → **Mots de passe pour app**.
3. Cliquer sur **+** / « Générer un mot de passe », le nommer **Amphi**.
4. Apple affiche un code `xxxx-xxxx-xxxx-xxxx` : le **copier tout de suite** (il n'est
   montré qu'une fois).
5. Dans Amphi : menu **Moodle → Configurer l'agenda Apple…**
6. Renseigner :
   - **Identifiant Apple** : l'adresse mail du compte Apple ;
   - **Mot de passe d'app** : le code de l'étape 4 ;
   - laisser cochée **Synchroniser au démarrage d'Amphi**.
7. Cliquer **Tester** → « ✓ Connexion iCloud OK — calendrier « Moodle IPSA » prêt ». Le
   calendrier est créé dans iCloud s'il n'existait pas.
8. Cliquer **Enregistrer**. Le mot de passe va dans le Gestionnaire d'identifiants Windows
   (jamais dans `config.json`).
9. Lancer une première synchro : bouton **⟳ Synchroniser Moodle** dans la tuile « MOODLE »
   (à gauche de l'écran d'accueil, ou **Ctrl+D**). Le texte sous le bouton affiche l'étape en
   cours (« Moodle : état des dépôts (3/9)… », « Apple : connexion à iCloud… »…), puis le tableau
   « Devoirs à faire | Documents non synchronisés » se met à jour et un résumé s'ouvre avec le
   même tableau.
10. Sur l'iPhone : app Calendrier → **Calendriers** (en bas) → vérifier que **Moodle IPSA**
    est coché.

### Ensuite

- À chaque lancement, Amphi synchronise en arrière-plan (5 s après l'ouverture) ; une fenêtre
  n'apparaît que s'il y a du nouveau ou une erreur. **Ctrl+D** force une synchro.
- La tuile « MOODLE » garde le tableau de la dernière synchro entre deux lancements (fichier
  `moodle_last.json`, ignoré par git) ; double-clic sur un devoir ouvre sa page Moodle.
- Moodle fait foi : un événement modifié ou supprimé à la main dans l'agenda est rétabli ; une
  échéance décalée par le prof est déplacée ; un devoir retiré de Moodle est supprimé.
- Ne sont **pas** mis dans l'agenda : les devoirs sans date limite et ceux déjà en retard
  (ils sont listés dans le résumé). Seules les sections « CAMPUS PARIS » / « … - PARIS » des
  cours de l'année sont lues ; les autres campus sont ignorés.
- **Nouveaux documents** : chaque synchro compare aussi les fichiers publiés sur Moodle (campus
  Paris) avec le dossier des cours défini dans les Réglages (`<dossier des cours>\<niveau>`).
  S'il en manque, le résumé liste « Nouveaux documents sur Moodle (N) — lance /maj-moodle dans
  Claude Code » ; au démarrage, la fenêtre ne s'ouvre que si cette liste a changé. Le skill
  Claude Code `/maj-moodle` (`.claude/skills/maj-moodle/`) les télécharge et les range
  (`Cours/`, `TP/TPn/`, `TD/TDn/`, `Projet/`, `DM/`) en tenant un registre
  `.moodle_sync.json` dans le dossier des cours. Le bouton **📥 Récupérer les documents
  (Claude)** de la tuile MOODLE ouvre une console où Claude Code lance `/maj-moodle` (il faut
  la commande `claude` installée) ; à la fermeture de la console, Amphi resynchronise et met le
  tableau à jour. Pour rendre le skill disponible depuis n'importe quel
  dossier, copier ce dossier dans `%USERPROFILE%\.claude\skills\maj-moodle\`. Utilisable aussi
  sans Claude :

  ```powershell
  .\.venv\Scripts\python .claude\skills\maj-moodle\driver.py plan    # rapport, sans écriture
  .\.venv\Scripts\python .claude\skills\maj-moodle\driver.py apply   # télécharge et range
  ```
- Dépannage :
  - « Identifiants iCloud refusés » → refaire un mot de passe d'app (étape B) ;
  - « token Moodle expiré » → refaire l'étape A ;
  - menu Moodle absent → Amphi n'a pas été relancé (Quitter depuis l'icône de la barre des
    tâches, puis rouvrir) ;
  - simulation sans rien écrire : `.\.venv\Scripts\python -m amphi.moodle sync --dry-run`.

## Architecture

```
amphi/
  audio/       capture (pause/marqueurs/anti-crash), encodage FLAC
  transcribe/  moteur faster-whisper, worker autonome, rendu Markdown, résumé + index des thèmes (Gemini)
  library/     index SQLite, découverte des matières
  moodle/      devoirs Moodle (lecture seule, campus Paris) → calendrier iCloud (CalDAV)
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
