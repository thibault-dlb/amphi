---
name: maj-moodle
description: Met à jour le dossier local des cours avec Moodle IPSA (campus Paris) — télécharge et range les nouveaux documents, récupère les fichiers modifiés ou manquants. À utiliser pour « mettre à jour les cours depuis Moodle », « récupérer les nouveaux documents Moodle », « synchroniser le local avec Moodle », « /maj-moodle », ou quand Amphi signale « Nouveaux documents sur Moodle — lance /maj-moodle ». Lançable depuis n'importe quel dossier.
---

# maj-moodle — mettre le dossier des cours à jour avec Moodle

Le travail passe par `driver.py` (dans ce dossier de skill), exécuté avec le Python du venv
d'Amphi : il lit Moodle (lecture seule, **campus Paris uniquement**), compare avec le dossier
local et un registre `.moodle_sync.json`, propose un rangement, puis télécharge.

- **Dossier des cours** = `<chemin des cours des Réglages d'Amphi>\<niveau>` (aujourd'hui
  `C:\Users\thibd\OneDrive\Cours\B3`) — jamais codé en dur ; `--root` pour forcer.
- **Amphi** est trouvé via `AMPHI_DIR`, sinon le dépôt qui contient ce skill, sinon `~/Amphi`.
  Le token Moodle est celui d'Amphi (`<Amphi>\.moodle_token`).
- Le skill vit dans le dépôt Amphi (`.claude/skills/maj-moodle/`, versionné sur GitHub) et en
  copie globale dans `~/.claude/skills/maj-moodle/` pour être lançable partout.

## Procédure (agent)

Commandes Bash (Git Bash). Ne rien télécharger avant d'avoir lu le `plan`.

```bash
PY=~/Amphi/.venv/Scripts/python.exe          # ou "$AMPHI_DIR/.venv/Scripts/python.exe"
D=~/.claude/skills/maj-moodle/driver.py
$PY $D plan --where                          # 1. chemins résolus (sans réseau)
$PY $D plan                                  # 2. rapport, aucune écriture
```

3. **Lire le rapport** par matière :
   - `NOUVEAUX` — fichier Moodle absent en local, avec la destination proposée et la raison.
     Vérifier la destination avec les conventions du dossier des cours (`CLAUDE.md` à sa
     racine) : `Cours/`, `TP/TPn/`, `TD/TDn/` (TP ou TD = **libellé Moodle Paris**), `Projet/`,
     `DM/` ; « Bases des données avancées » a un `TP/` à plat. Une destination « à répartir »
     ou `À classer/` demande une décision : regarder le contenu du fichier (texte des
     `.pptx`/`.docx` = XML dans le zip) ou demander à l'utilisateur.
   - `MANQUANTS EN LOCAL` — enregistré mais supprimé/déplacé en local : `apply` le
     re-télécharge à son ancien chemin (demander si l'utilisateur l'a supprimé exprès →
     `--ignore`).
   - `MAJ_DISPO` — le prof a remplacé le fichier sur Moodle → étape 5.
   - `DEJA_PRESENT` / `DEJA_PRESENT_RENOMME` — trouvé en local (même nom / même taille) ; `apply`
     l'enregistre. Si le rapport affiche « ATTENTION : vérifier », contrôler.
4. **Appliquer** (adoptions + téléchargements), avec les corrections décidées :

   ```bash
   $PY $D apply --dry-run [options]           # simulation
   $PY $D apply [options]
   ```

   Options (clés = `cle :` du rapport, chemins relatifs au **dossier de la matière**) :
   - `--dest "CLE=TP/TP3/"` — autre destination ;
   - `--adopt "CLE=Cours/nom local.pdf"` — un fichier local existant correspond (renommé,
     contenu vérifié) : enregistré, pas téléchargé ;
   - `--ignore CLE` — ne plus jamais proposer (ex. archive déjà extraite) ;
   - `--only CLE …` — ne télécharger que ces fichiers.
   Exemple réellement exécuté :

   ```bash
   $PY $D apply --adopt "7389/98403/BaseDesDonnées_1.2.pdf=Cours/Bases de données relationnelles.pdf" --adopt "7389/98403/BaseDesDonnées_1.3.pdf=Cours/Modifier la structure de la base de donnée.pdf" --ignore "7390/101307/K means clustering.zip"
   ```

5. **Mises à jour Moodle** : `$PY $D update` — remplace sur place si l'utilisateur n'a pas
   modifié le fichier local (même SHA-1 qu'au téléchargement), sinon enregistre la nouvelle
   version à côté (`<nom> (moodle JJ-MM)<ext>`) et le signale.
6. **Sauvegarde git** du dossier des cours (c'est un dépôt) : n'ajouter **que** les lignes
   `ÉCRIT: …` affichées + `.moodle_sync.json`, jamais `git add -A` :

   ```bash
   cd "<dossier des cours>" && git add .moodle_sync.json "<chemin ÉCRIT 1>" … && git commit -m "Moodle : …"
   ```

7. `$PY $D plan` doit finir sur `NOUVEAU : 0 … MANQUANT_LOCAL : 0`. Résumer à l'utilisateur ce
   qui a été ajouté et où.

## Gotchas

- **Jamais d'autre campus.** Seules les sections « CAMPUS PARIS » / « … - PARIS » (+ section 0)
  sont lues ; si un cours a une section PARIS, les sections sans « PARIS » sont un autre campus
  (ex. Data Mining : « Courses » / « Practical Work »). Ne pas contourner.
- **`git add -A` est dangereux** dans le dossier des cours : Amphi peut être en train d'écrire
  un enregistrement (`<Matière>/Enregistrements/…`, transcription en cours). Ajouter fichier
  par fichier.
- **Adoption par taille** : `DEJA_PRESENT_RENOMME` se fonde sur une taille identique — un
  fichier sans rapport de même taille serait adopté ; le rapport montre le nom proche, le
  vérifier. À l'inverse, un PDF renommé **et** réenregistré n'a plus la même taille → sort en
  `NOUVEAU` ; comparer le texte puis `--adopt`.
- **Archives** (`.zip`) : souvent déjà extraites en local → `--ignore` après vérification du
  contenu.
- Les profs ajoutent des fichiers en cours de journée : un `plan` « à jour » le matin peut
  ne plus l'être l'après-midi. Amphi le signale à chaque synchro des devoirs.
- `plan --json` donne la liste machine (clés, statuts, destinations) pour scripter `--dest`.

## Dépannage (rencontré)

| Symptôme | Cause / correction |
|---|---|
| `ERREUR : la variable AMPHI_DIR (…) ne pointe pas vers un dépôt Amphi`, exit 2 | corriger/supprimer `AMPHI_DIR` |
| `ERREUR : dossier racine introuvable` / « le dossier des cours défini dans les Réglages d'Amphi n'existe pas », exit 2 | mauvais `--root`, ou Réglages d'Amphi (chemin/niveau) à corriger |
| « token Moodle expiré », exit 2 | refaire « Étape A — Connexion à Moodle » du README d'Amphi |
| Accents illisibles dans un terminal | affichage seulement ; `export PYTHONIOENCODING=utf-8` |
