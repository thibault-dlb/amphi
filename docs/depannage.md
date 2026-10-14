# Dépannage

Premier réflexe : les journaux dans `logs\` — `amphi.log` (interface, synchro Moodle),
`worker.log` (transcription, résumés), `moodle.log` (ligne de commande Moodle).

## Enregistrement

| Symptôme | Cause probable / solution |
|---|---|
| Alerte « plus aucun signal micro » | micro coupé (touche muet, Réglages Windows) ou mauvais périphérique : Réglages → Micro & moteur |
| Son trop faible | augmenter `mic_gain_db` ; `normalize_speech` aide aussi la transcription |
| Compteur de pertes audio qui monte | CPU saturé : désactiver `transcribe_while_recording` pendant les cours |
| Amphi a planté pendant un cours | relancer : l'enregistrement est retrouvé dans `.staging\` et proposé au classement |

## Transcription

| Symptôme | Cause probable / solution |
|---|---|
| Reste « en pause (batterie) » | brancher le secteur, ou « ⚡ Transcrire maintenant (batterie) » |
| Reste « en attente » sans démarrer | worker arrêté ou bloqué : voir `worker.log` ; supprimer `.worker.lock` si aucun processus Python du worker ne tourne |
| « échec transcription » | lire l'erreur dans la file et `worker.log`, puis « Réessayer » |
| Texte inventé sur les silences | vérifier que `vad_filter` est actif |
| Termes techniques mal transcrits | ajouter `vocabulaire.txt` ou un prompt de matière (voir [transcription.md](transcription.md)) |
| Trop lent | garder `large-v3-turbo`, baisser `beam_size`, relancer le benchmark |

## Résumé et index

| Symptôme | Cause probable / solution |
|---|---|
| « résumé échoué » avec 503 / saturé | Gemini saturé : « Relancer le résumé » plus tard ; ajuster `fallback_models` |
| Quota du jour épuisé | attendre le lendemain, ou un autre modèle de repli |
| Pas de résumé du tout | pas de clé, `enabled` ou `auto_after_transcription` désactivé, ou transcript trop court |
| `INDEX.md` absent | `index_themes` désactivé, ou lancer `python -m amphi.transcribe.themes --all` |

## Moodle et agenda

| Message | Cause / solution |
|---|---|
| `Moodle injoignable : [Errno 11001] getaddrinfo failed` | le nom `moodle.ipsa.fr` n'a pas pu être résolu : pas d'Internet à ce moment (Wi-Fi qui se reconnecte, coupure). Amphi a déjà retenté 5 fois ; relancer « Synchroniser ». Si Internet marche, vérifier DNS / VPN |
| `Moodle injoignable : …` (autre erreur réseau) | coupure ou Moodle en panne ; réessayer plus tard |
| « token Moodle expiré » / « Token Moodle introuvable » | refaire l'étape A (README) |
| « Identifiants iCloud refusés » | refaire un mot de passe d'app Apple (étape B) |
| Moodle HTTP 4xx/5xx | problème côté Moodle ; réessayer plus tard |
| Un cours n'apparaît pas | le compte n'y est pas inscrit, ou ce n'est pas un cours de l'année courante |
| Les documents d'un cours vont dans un dossier inattendu | le nom du dossier vient du nom Moodle : ajouter une exception dans `MATIERES_FORCEES` (voir [moodle.md](moodle.md)) |
| Menu Moodle absent | Amphi n'a pas été relancé après une mise à jour : Quitter depuis l'icône de notification, puis rouvrir |
| 📥 Récupérer ne fait rien | la commande `claude` est introuvable dans le PATH |

Tester sans rien écrire :

```powershell
.\.venv\Scripts\python -m amphi.moodle sync --no-icloud   # lit Moodle, sans iCloud
.\.venv\Scripts\python -m amphi.moodle sync --dry-run     # simule contre le vrai agenda
```
