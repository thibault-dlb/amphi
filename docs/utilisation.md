# Utilisation

## La fenêtre

Au lancement, Amphi s'ouvre **en plein écran sur le ScreenPad** avec trois tuiles côte à
côte : **Moodle**, **Enregistrement**, **File d'attente**. C'est une fenêtre Windows normale
(redimensionnable, `Win`+flèches, minimisation, icône dans la zone de notification).

Réglages → *Comportement* → position au lancement :

- **plein écran** (défaut) ;
- **moitié droite** / **moitié gauche** : vrai snap Windows, une autre app ancrée sur l'autre
  moitié se redimensionne avec Amphi ;
- ancrage automatique désactivé.

`Ctrl+Home` (menu **Affichage**) ré-ancre la fenêtre sur le ScreenPad. **Affichage → Toujours
au-dessus** garde Amphi devant les autres fenêtres.

Fermer la fenêtre ne quitte pas Amphi : utiliser **Fichier → Quitter** (`Ctrl+Q`) ou
l'icône de la zone de notification → **Quitter**.

## Enregistrer un cours

1. Gros bouton rouge : **démarrer**. La veille Windows est bloquée pendant l'enregistrement
   (réglage `prevent_sleep`).
2. Pendant le cours :
   - **⏸** ou `Ctrl+P` : pause / reprise. Le flux micro reste ouvert, aucune coupure audible à
     la reprise ; les pauses ne figurent pas dans l'enregistrement.
   - **＋** ou `Ctrl+M` : **marqueur « important »**. Il apparaît dans le transcript
     (`> ⭐ [hh:mm:ss] — marqueur « important »`) et dans une section récapitulative.
   - L'indicateur de niveau signale un micro muet ou silencieux depuis 12 s, et les pertes
     d'audio (dépassements de buffer).
3. Bouton rouge à nouveau : **arrêter**. Un dialogue demande **matière**, **titre**, **date**
   et **langue** (fr / en), puis l'enregistrement part dans la file d'attente.

Le dossier final est
`<dossier des cours>\<Niveau>\<Matière>\Enregistrements\<AAAA-MM-JJ>_<titre>\`.

### Si Amphi plante pendant un cours

La capture est écrite en continu dans `.staging\<id>\capture.wav`, avec un fichier
`recording.json` réécrit toutes les 2 s (durée, marqueurs). Au redémarrage, Amphi retrouve
l'enregistrement non classé et propose **Classer maintenant**, **Garder pour plus tard** ou
**Supprimer** ; un enregistrement suspendu peut aussi être **repris**. Le WAV est réparé par
ffmpeg si besoin.

## La file d'attente

Chaque enregistrement validé passe par les étapes affichées dans la tuile de droite :
`en attente` → `transcription hh:mm:ss / hh:mm:ss` → `transcript prêt` → `résumé…` →
`index des thèmes…` → `terminé`. Détail dans [transcription.md](transcription.md).

Actions disponibles selon l'état :

| Bouton | Quand |
|---|---|
| ⏸ Pause / ▶ Reprendre | en attente ou en cours de transcription / en pause |
| ⚡ Transcrire maintenant (batterie) | file en pause parce que le laptop est sur batterie (dérogation ponctuelle) |
| Réessayer / Retirer | échec de transcription |
| Relancer le résumé | résumé en échec (saturation Gemini, quota…) |
| Relancer l'index | index des thèmes en échec |
| Ouvrir | transcript livré : ouvre le dossier de l'enregistrement |

## Raccourcis

| Action | Raccourci |
|---|---|
| Pause / reprise de l'enregistrement | `Ctrl+P` |
| Marqueur « important » | `Ctrl+M` |
| Réglages | `Ctrl+,` |
| Quitter | `Ctrl+Q` |
| Ré-ancrer sur le ScreenPad | `Ctrl+Home` |
| Synchroniser Moodle | `Ctrl+D` |

## Ce qui est produit

```
<Matière>\Enregistrements\<date>_<titre>\
  audio.flac       audio recompressé sans perte (ou .wav / .opus / rien, selon le réglage)
  transcript.md    transcription horodatée par paragraphes + marqueurs
  resume.md        plan + synthèse + définitions/formules (si Gemini est configuré)
  meta.json        titre, matière, date, durée, marqueurs, réglages du moteur
<Matière>\INDEX.md                          index des thèmes de la matière
<Matière>\Enregistrements\index_themes.json données de l'index (ne pas éditer)
<Matière>\Enregistrements\vocabulaire.txt   vocabulaire technique (facultatif, voir transcription.md)
```

`transcript.md` commence par un en-tête YAML (`titre`, `matiere`, `niveau`, `date`,
`langue`, `duree`, `marqueurs`, `modele`) suivi de paragraphes `**[hh:mm:ss]** texte…`.
