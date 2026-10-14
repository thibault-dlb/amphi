# Résumé et index des thèmes (Gemini)

Facultatif : sans clé API, Amphi livre seulement le transcript. Le transcript est envoyé en
ligne à Google ; Amphi l'indique une fois (`acknowledged_privacy`).

## Clé API

Créer une clé gratuite sur <https://aistudio.google.com/apikey>, la coller dans
**Réglages → Résumé IA**, puis « Tester ». L'abonnement Google AI Plus ne donne **pas** de
clé API.

## Le résumé (`resume.md`)

Lancé automatiquement après la transcription (`auto_after_transcription`), sauf si le
transcript est vide ou trop court (< 150 caractères). Le prompt par défaut demande, en
français et en Markdown, exactement trois sections :

1. **Plan du cours** — plan hiérarchique fidèle à la progression ;
2. **Synthèse** — prose dense, section par section ;
3. **Définitions, théorèmes et formules clés** — une puce par notion, formules en LaTeX.

Gemini ne doit rien inventer : un passage mal transcrit est signalé
`[passage incertain : "..."]`, et un point sur lequel le prof insiste est marqué 🔑. Le prompt
est remplaçable dans les Réglages (`prompt_override`).

## Modèles et saturation

- Modèle principal : `gemini-flash-latest` (alias maintenu par Google vers le Flash stable).
- Modèles de repli : liste `gemini.fallback_models` de `config.json`, essayés dans l'ordre.
- Pour chaque modèle, jusqu'à 4 essais ; sur saturation (503/429), attente de 5, 10 puis
  20 s. Un modèle introuvable pour la clé ou dont le quota du jour est épuisé est sauté
  aussitôt.
- Chaque requête est coupée au bout de **180 s** (sinon une requête « avalée » par un Gemini
  saturé reste pendante 15–30 min).
- L'étape en cours (modèle, essai, attente) s'affiche dans la file d'attente.
- Si tout échoue : statut « résumé échoué », bouton **Relancer le résumé**. Le transcript, lui,
  est déjà livré.

`thinking_budget` (défaut 4096 ; `-1` = dynamique, `0` = désactivé) règle le raisonnement du
modèle.

## L'index des thèmes (`INDEX.md`)

Deuxième requête, enchaînée après le résumé (`index_themes`, activé par défaut). Il permet à
un agent IA (Claude Code…) de retrouver où chaque notion est traitée sans relire tous les
transcripts.

```
<Matière>\INDEX.md                          thèmes → passages (cours, lignes du transcript, nature, note)
<Matière>\Enregistrements\index_themes.json données source (ne pas éditer)
```

Fonctionnement :

- Gemini reçoit la **liste des thèmes déjà indexés** et le transcript du nouveau cours, aux
  **lignes numérotées**. Il renvoie, en JSON contraint par un schéma, uniquement l'apport de
  ce cours : thèmes existants ou nouveaux, passages (plage de lignes), nature
  (`définition`, `explication`, `exemple`, `exercice`, `rappel`, `mention`) et une note courte.
- Amphi **fusionne** dans `index_themes.json` ; les références des cours précédents ne
  repassent jamais par le modèle.
- `INDEX.md` est réécrit à chaque mise à jour ; les cours dont le transcript a disparu sont
  retirés.
- Un verrou (`.index.lock`) empêche deux écritures simultanées.

### Indexer les cours déjà transcrits

Réglages → Résumé IA → « Indexer les cours déjà transcrits… », ou :

```powershell
.\.venv\Scripts\python -m amphi.transcribe.themes --all
.\.venv\Scripts\python -m amphi.transcribe.themes --all --matiere "Machine learning"
.\.venv\Scripts\python -m amphi.transcribe.themes --all --force     # réindexe même les cours déjà indexés
.\.venv\Scripts\python -m amphi.transcribe.themes --all --dry-run   # montre la requête sans l'envoyer
```
