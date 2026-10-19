# Moodle IPSA

Amphi lit Moodle IPSA (<https://moodle.ipsa.fr>) **en lecture seule**, pour deux usages :

1. **Devoirs → agenda Apple** : les devoirs sont copiés dans un calendrier iCloud
   « Moodle IPSA », visible sur l'iPhone ;
2. **Documents → dossier des cours** : les fichiers publiés sur Moodle sont comparés au
   dossier local ; le skill Claude Code `/maj-moodle` télécharge et range ceux qui manquent.

Seuls les cours de **l'année courante** (préfixe `26-` du nom Moodle, la plus récente) et les
sections du **campus Paris** sont lus : sections dont le nom contient « PARIS », plus la
section 0. Un cours sans aucune section « PARIS » est lu en entier. Les autres campus sont
toujours ignorés.

Exception pour les **devoirs** : tout le cours est lu, sections sans campus comprises
(ex. Data Mining : « Practical Work »). Seules sont écartées les sections d'un autre campus
(`AUTRES_CAMPUS` dans `amphi/moodle/client.py`, aujourd'hui `TOULOUSE`), c'est-à-dire celles
dont le nom le contient ou qui sont placées sous un en-tête « CAMPUS TOULOUSE ». Les
**documents** restent limités au campus Paris.

## Mise en place

Deux connexions à faire une seule fois. La procédure pas à pas (captures du token, mot de
passe d'app Apple) est dans le [README](../README.md#devoirs-moodle--agenda-apple) :

- **Étape A — token Moodle** : récupéré par le navigateur (la connexion IPSA passe par
  Office 365), enregistré dans `.moodle_token`. Vérification :
  `.\.venv\Scripts\python -m amphi.moodle sync --no-icloud`.
- **Étape B — iCloud** : mot de passe d'app Apple saisi dans **Moodle → Configurer l'agenda
  Apple…**, stocké dans le Gestionnaire d'identifiants Windows (service `amphi-icloud`), jamais
  dans `config.json`.

## Correspondance cours Moodle ↔ dossier local

Le nom du dossier d'une matière est **déduit du nom complet du cours Moodle** : Amphi retire
le préfixe `AA-CODE-`, remplace `:` par `-` et retire les caractères interdits sous Windows.

```
26-BIN313-Apprentissage automatique : Machine learning
    → <dossier des cours>\B3\Apprentissage automatique - Machine learning
```

Quand le nom Moodle ne correspond pas au dossier local, une exception est déclarée par
**code de cours** dans `MATIERES_FORCEES` (`amphi/moodle/client.py`) :

| Code | Dossier local | Pourquoi |
|---|---|---|
| `PBI` | `Projet Bachelor IPSA` | le nom Moodle change chaque année (« Projet Bachelor IPSA » en 2025, « Lancement Bachelor IPSA (PBI) appliqués à l'aéronautique (IA) » en 2026, en trois variantes FISA / FISE AD / FISE IA) |

Le code suit le préfixe d'année : `26-PBI-…` → `PBI`. Pour un autre cours au nom
trompeur, ajouter une ligne à la table. Le nom de matière sert partout : titre des événements
de l'agenda, rapport de synchro, rangement des documents.

Seuls les cours **auxquels le compte est inscrit** sont lus. Pour vérifier si un cours existe
sans y être inscrit, `core_course_search_courses` le trouve, mais Amphi ne pourra le
synchroniser qu'après l'inscription.

## Synchronisation des devoirs

Déclenchement : au lancement d'Amphi (5 s après l'ouverture, si `sync_on_startup`), avec le
bouton **⟳ Synchroniser** de la tuile MOODLE, ou `Ctrl+D`.

Règles :

- **Moodle fait foi.** Chaque événement porte une empreinte (`X-AMPHI-HASH`) ; s'il diffère de
  ce que Moodle donne (modification à la main, échéance décalée par le prof), il est réécrit.
  Un devoir retiré de Moodle est supprimé de l'agenda.
- Seuls les événements Amphi (UID `amphi-moodle-assign-<id>@moodle.ipsa.fr`) sont touchés : le
  reste du calendrier n'est jamais modifié.
- Événement : d'une heure avant l'échéance jusqu'à l'échéance, titre
  `📝 <devoir> — <matière>`, description avec l'état, le lien de dépôt et les consignes.
- **3 rappels** : J-3, J-1, 3 h avant. Un devoir **rendu** reste dans l'agenda, marqué ✅, sans
  rappel.
- Pas mis dans l'agenda : les devoirs **sans date limite** et ceux **déjà en retard** (listés
  dans le rapport).

Le tableau de la tuile MOODLE (« Devoirs | Documents non synchronisés ») liste tous les devoirs
dans cet ordre : à rendre (les « sans date » en fin de groupe), en retard, fermés, rendus ; dans
chaque groupe, l'échéance la plus proche de maintenant d'abord. Il est conservé
entre deux lancements dans `moodle_last.json`. Double-clic sur un devoir : ouvre sa page
Moodle.

### En ligne de commande

```powershell
.\.venv\Scripts\python -m amphi.moodle sync              # synchro réelle
.\.venv\Scripts\python -m amphi.moodle sync --dry-run    # simule, n'écrit rien dans iCloud
.\.venv\Scripts\python -m amphi.moodle sync --no-icloud  # lit Moodle seulement (sans identifiants Apple)
```

Code de sortie 1 s'il y a des erreurs.

### Réseau

Chaque appel Moodle est retenté **5 fois, à 3 s d'intervalle** sur erreur réseau (Wi-Fi qui se
reconnecte après une veille, coupure brève), avant d'afficher « Moodle injoignable ».

## Documents Moodle → dossier des cours

Chaque synchro compare aussi les fichiers publiés (ressources et dossiers Moodle, campus Paris)
avec `<dossier des cours>\<niveau>`. S'il en manque, ils apparaissent dans la colonne
« Documents non synchronisés » du tableau. Aucune fenêtre ne s'ouvre après une synchro : une
erreur s'affiche sous le bouton, et le rapport complet est écrit dans `amphi.log`.

### Bouton 📥 Récupérer

À côté de « Synchroniser » dans la tuile MOODLE :

- **clic** : ouvre une console où Claude Code (`--model sonnet`) exécute `/maj-moodle` en
  **mode automatique** (`claude -p`). Aucune question : un document à la destination ambiguë va
  dans `À classer/`. Les permissions sont limitées au script du skill et à la lecture. La
  console se ferme dès la fin, puis Amphi resynchronise et met le tableau à jour ;
- **clic droit** : menu « Récupérer automatiquement » / « Ouvrir Claude en mode
  interactif… » (session Claude normale, fermée quand on quitte Claude).

Il faut la commande `claude` dans le PATH.

### Le skill `/maj-moodle`

Fourni dans `.claude/skills/maj-moodle/` (`SKILL.md` + `driver.py`). Pour le lancer depuis
n'importe quel dossier, copier ce dossier dans `%USERPROFILE%\.claude\skills\maj-moodle\`.

Rangement dans le dossier de la matière : `Cours/`, `TP/TPn/`, `TD/TDn/` (numéro pris du
libellé Moodle Paris), `Projet/`, `DM/`, sinon `À classer/`. Un registre `.moodle_sync.json` à
la racine du dossier des cours garde, pour chaque fichier Moodle, son chemin local, sa taille,
sa date et son SHA-1.

Le skill ne touche jamais aux dossiers `Enregistrements/` ni aux `INDEX.md`, gérés par Amphi.

Utilisable sans Claude :

```powershell
$D = ".claude\skills\maj-moodle\driver.py"
.\.venv\Scripts\python $D plan --where        # chemins résolus, sans réseau
.\.venv\Scripts\python $D plan                # rapport, aucune écriture
.\.venv\Scripts\python $D apply --dry-run     # simulation
.\.venv\Scripts\python $D apply               # télécharge et range
.\.venv\Scripts\python $D update              # applique les fichiers remplacés sur Moodle
```

Catégories du rapport : `NOUVEAUX`, `MANQUANTS EN LOCAL`, `MAJ_DISPO`, `DEJA_PRESENT`,
`DEJA_PRESENT_RENOMME`, `OK`, `IGNORE`. Options de `apply` : `--dest "CLE=TP/TP3/"`,
`--adopt "CLE=Cours/nom local.pdf"`, `--ignore CLE`, `--only CLE …`.

`update` remplace un fichier sur place seulement s'il n'a pas été modifié en local (même
SHA-1 qu'au téléchargement) ; sinon la nouvelle version est enregistrée à côté,
`<nom> (moodle JJ-MM)<ext>`.

## Secrets

| Secret | Où |
|---|---|
| Token Moodle | `.moodle_token` (ignoré par git) |
| Lien iCal Moodle | `.moodle_ical_url` (ignoré par git) |
| Identifiant + mot de passe d'app Apple | Gestionnaire d'identifiants Windows, service `amphi-icloud` |

Le token et le mot de passe ne sont jamais journalisés. Révoquer le token : Moodle →
Préférences → Clés de sécurité.
