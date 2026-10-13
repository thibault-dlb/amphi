"""Synchronise les dossiers de cours locaux (OneDrive) avec les fichiers publiés sur Moodle IPSA.

Périmètre : campus PARIS uniquement (section 0 + sections marquées PARIS).
Côté Moodle, seules des fonctions de LECTURE sont appelées. Le token n'est jamais
affiché ni journalisé.

La logique (classification, manifeste, téléchargement) est dans amphi.moodle.fichiers.

Commandes :
  plan    [--json] [--root PATH]
          Classe tous les fichiers Moodle et affiche un rapport. Aucune écriture.
  apply   [--root PATH] [--only CLE ...] [--dest CLE=REL_DIR ...] [--ignore CLE ...]
          [--adopt CLE=CHEMIN_REL ...] [--dry-run]
          Enregistre les fichiers déjà présents, ignore ceux demandés, télécharge les nouveaux.
  update  [--root PATH] [--only CLE ...] [--dry-run]
          Met à jour les fichiers dont la version Moodle est plus récente.

Sans commande, « plan » est exécuté.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def _trouver_amphi() -> tuple[Path | None, str]:
    """Dossier du dépôt Amphi : AMPHI_DIR, puis le dépôt qui contient ce skill, puis ~/Amphi."""
    candidats: list[tuple[Path, str]] = []
    env = os.environ.get("AMPHI_DIR", "").strip()
    if env:
        # Variable définie : elle fait foi, pas de repli silencieux.
        dossier_env = Path(env).expanduser()
        if (dossier_env / "amphi" / "__init__.py").is_file():
            return dossier_env, "variable AMPHI_DIR"
        return None, ""
    try:
        candidats.append((Path(__file__).resolve().parents[3], "dépôt contenant ce skill"))
    except IndexError:
        pass
    candidats.append((Path.home() / "Amphi", "dossier ~/Amphi"))
    for dossier, origine in candidats:
        if (dossier / "amphi" / "__init__.py").is_file():
            return dossier, origine
    return None, ""


AMPHI_DIR, ORIGINE_AMPHI = _trouver_amphi()
if AMPHI_DIR is None:
    if os.environ.get("AMPHI_DIR", "").strip():
        print(f"ERREUR : la variable AMPHI_DIR ({os.environ['AMPHI_DIR']}) ne pointe pas vers un dépôt "
              "Amphi (amphi/__init__.py introuvable). Corrigez-la vers le dossier du dépôt Amphi.",
              file=sys.stderr)
    else:
        print("ERREUR : dossier Amphi introuvable (ni le dépôt de ce skill, ni ~/Amphi). "
              "Définissez la variable d'environnement AMPHI_DIR vers le dossier du dépôt Amphi.",
              file=sys.stderr)
    sys.exit(2)
sys.path.insert(0, str(AMPHI_DIR))

from amphi.moodle.client import MoodleError, MoodleTokenError, load_token  # noqa: E402
from amphi.moodle.fichiers import (  # noqa: E402
    CLASSES_ORDRE,
    TITRES_CLASSES,
    ErreurFichier,
    ErreurRacine,
    ErreurReseau,
    charger_tout,
    cible_relative,
    date_lisible,
    ecrire_manifeste,
    installer,
    racine_des_cours,
    resoudre_racine,
    sha1_fichier,
    taille_lisible,
    telecharger,
)


def _afficher_progression(message: str) -> None:
    print(message, file=sys.stderr)


def _entete(racine: Path, origine: str, json_sortie: bool = False) -> None:
    """Affiche le dossier des cours retenu (stderr si la sortie est du JSON)."""
    flux = sys.stderr if json_sortie else sys.stdout
    print(f"Dossier des cours : {racine} ({origine})", file=flux)


def cmd_where(args: argparse.Namespace) -> int:
    """Affiche le dossier Amphi et le dossier des cours résolus, sans réseau."""
    racine, origine = racine_des_cours(args.root)
    print(f"Dossier Amphi : {AMPHI_DIR} ({ORIGINE_AMPHI})")
    etat = "existe" if racine.is_dir() else "N'EXISTE PAS"
    print(f"Dossier des cours : {racine} ({origine}, {etat})")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    if args.where:
        return cmd_where(args)
    racine = resoudre_racine(args.root)
    _entete(racine, "--root" if args.root else "Réglages d'Amphi", args.json)
    resultats, _ = charger_tout(racine, _afficher_progression)
    if args.json:
        sortie = [
            {k: e.get(k) for k in (
                "key", "cours", "matiere", "section", "module", "modname", "filename",
                "filesize", "timemodified", "fileurl", "classe", "local", "cible",
                "dest_dir", "raison", "a_repartir", "nom_proche", "similarite", "attention")}
            for e in resultats
        ]
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
        return 0
    afficher_plan(resultats, racine)
    return 0


def afficher_plan(resultats: list[dict[str, Any]], racine: Path) -> None:
    """Rapport lisible, groupé par matière."""
    totaux: Counter[str] = Counter()
    for matiere in sorted({e["matiere"] for e in resultats}):
        groupe = [e for e in resultats if e["matiere"] == matiere]
        print(f"\n### {matiere}   ({racine / matiere})")
        for classe in CLASSES_ORDRE:
            liste = [e for e in groupe if e["classe"] == classe]
            totaux[classe] += len(liste)
            if not liste:
                continue
            print(f"\n  {TITRES_CLASSES[classe]} : {len(liste)}")
            for e in liste:
                print(f"    - {e['filename']}  [{taille_lisible(e['filesize'])}]  "
                      f"({e['section']} / {e['module']})")
                print(f"      clé : {e['key']}")
                if classe == "NOUVEAU":
                    marque = "  [à répartir]" if e["a_repartir"] else ""
                    print(f"      → {e['cible']}  ({e['raison']}){marque}")
                elif classe == "MAJ_DISPO":
                    print(f"      locale : {e['local']} ; Moodle modifié le {date_lisible(e['timemodified'])}")
                elif classe == "MANQUANT_LOCAL":
                    print(f"      → {e['cible']} (absent du dossier)")
                elif classe == "DEJA_PRESENT":
                    print(f"      = {e['local']}")
                else:
                    print(f"      ~ {e['local']}  (nom proche, mots communs : {e['mots_communs']}, "
                          f"similarité {e['similarite']:.0%})")
                if e["attention"]:
                    print(f"      ATTENTION : {e['attention']}")
        ok = sum(1 for e in groupe if e["classe"] == "OK")
        ign = sum(1 for e in groupe if e["classe"] == "IGNORE")
        totaux["OK"] += ok
        totaux["IGNORE"] += ign
        print(f"\n  OK : {ok}   IGNORÉ : {ign}")

    print("\n=== RÉSUMÉ ===")
    for classe in CLASSES_ORDRE:
        print(f"  {classe} : {totaux[classe]}")
    print(f"  OK : {totaux['OK']}   IGNORÉ : {totaux['IGNORE']}")


def _parser_cles(valeurs: list[str] | None) -> dict[str, str]:
    """Transforme « CLE=REL_DIR » en dictionnaire, en normalisant le dossier."""
    resultat: dict[str, str] = {}
    for v in valeurs or []:
        if "=" not in v:
            raise SystemExit(f"ERREUR : --dest attend CLE=REL_DIR, reçu « {v} »")
        cle, rel = v.split("=", 1)
        rel = rel.replace("\\", "/").strip("/")
        resultat[cle.strip()] = f"{rel}/" if rel else ""
    return resultat


def _parser_adoptions(valeurs: list[str] | None) -> dict[str, str]:
    """Transforme « CLE=CHEMIN_REL » en dictionnaire (chemin relatif au dossier de la matière)."""
    resultat: dict[str, str] = {}
    for v in valeurs or []:
        if "=" not in v:
            raise SystemExit(f"ERREUR : --adopt attend CLE=CHEMIN_REL, reçu « {v} »")
        cle, rel = v.split("=", 1)
        rel = rel.replace("\\", "/").strip("/")
        if not rel:
            raise SystemExit(f"ERREUR : --adopt : chemin vide pour la clé « {cle} »")
        resultat[cle.strip()] = rel
    return resultat


def cmd_apply(args: argparse.Namespace) -> int:
    racine = resoudre_racine(args.root)
    _entete(racine, "--root" if args.root else "Réglages d'Amphi")
    resultats, manifeste = charger_tout(racine, _afficher_progression)
    cles_moodle = {e["key"] for e in resultats}
    par_cle = {e["key"]: e for e in resultats}
    ignores = set(args.ignore or [])
    seulement = set(args.only or [])
    overrides = _parser_cles(args.dest)
    adoptions = _parser_adoptions(args.adopt)
    for cle in set(adoptions) & ignores:
        raise SystemExit(f"ERREUR : --adopt et --ignore pour la même clé : {cle}")
    for cle in (ignores | seulement | set(overrides)) - cles_moodle:
        print(f"AVERTISSEMENT : clé inconnue (pas un fichier Moodle du campus Paris) : {cle}")

    erreurs = 0
    compteur: Counter[str] = Counter()
    token = load_token() if not args.dry_run else ""

    def enregistrer() -> None:
        if not args.dry_run:
            ecrire_manifeste(racine, manifeste)

    # Adoptions explicites : le fichier local existe déjà, on le rattache sans téléchargement.
    adoptes_cles: set[str] = set()
    for cle, rel in adoptions.items():
        e = par_cle.get(cle)
        if e is None:
            erreurs += 1
            print(f"ERREUR : --adopt : clé inconnue (pas un fichier Moodle du campus Paris) : {cle}",
                  file=sys.stderr)
            continue
        rel_racine = f"{e['matiere']}/{rel}"
        chemin = racine / rel_racine
        if not chemin.is_file() or not chemin.resolve().is_relative_to(racine):
            erreurs += 1
            print(f"ERREUR : --adopt : fichier introuvable : {rel_racine}", file=sys.stderr)
            continue
        deja = [k for k, m in manifeste.items()
                if k != cle and m.get("statut") == "local" and m.get("local") == rel_racine]
        if deja:
            erreurs += 1
            print(f"ERREUR : --adopt : {rel_racine} est déjà rattaché à une autre clé",
                  file=sys.stderr)
            continue
        adoptes_cles.add(cle)
        compteur["adoptes"] += 1
        if args.dry_run:
            print(f"[simulation] ADOPTER : {rel_racine}  (clé {cle}, --adopt)")
            continue
        manifeste[cle] = {"statut": "local", "local": rel_racine, "taille": e["filesize"],
                          "modifie": e["timemodified"], "sha1": sha1_fichier(chemin)}
        print(f"ADOPTÉ : {rel_racine}  (--adopt)")

    for e in resultats:
        cle = e["key"]

        if cle in adoptes_cles:
            continue

        if cle in ignores:
            compteur["ignores"] += 1
            print(f"IGNORÉ : {cle}")
            if not args.dry_run:
                manifeste[cle] = {"statut": "ignore", "local": None, "taille": e["filesize"],
                                  "modifie": e["timemodified"], "sha1": ""}
            continue

        if e["classe"] in ("DEJA_PRESENT", "DEJA_PRESENT_RENOMME"):
            if args.dry_run:
                compteur["adoptes"] += 1
                print(f"[simulation] ADOPTER : {e['local']}  (clé {cle})")
                continue
            sha = sha1_fichier(racine / e["local"])
            manifeste[cle] = {"statut": "local", "local": e["local"], "taille": e["filesize"],
                              "modifie": e["timemodified"], "sha1": sha}
            compteur["adoptes"] += 1
            print(f"ADOPTÉ : {e['local']}" + (f"  ({e['attention']})" if e["attention"] else ""))
            continue

        if e["classe"] not in ("NOUVEAU", "MANQUANT_LOCAL"):
            continue
        if seulement and cle not in seulement:
            continue

        if e["classe"] == "MANQUANT_LOCAL":
            cible_rel = e["cible"]
        elif cle in overrides:
            if overrides[cle] == "":
                print(f"AVERTISSEMENT : --dest vide ignoré pour {cle}")
            cible_rel = cible_relative(e["matiere"], overrides[cle] or e["dest_dir"], e["filename"])
        else:
            cible_rel = e["cible"]

        if args.dry_run:
            compteur["ecrits"] += 1
            print(f"[simulation] TÉLÉCHARGER : {cible_rel}  (clé {cle}, {taille_lisible(e['filesize'])})")
            continue

        cible = racine / cible_rel
        try:
            tmp, sha = telecharger(e["fileurl"], token, cible.parent, e["filesize"], e["filename"])
            statut, final = installer(tmp, cible, sha, "moodle")
        except ErreurReseau as err:
            enregistrer()
            print(f"ERREUR RÉSEAU : {err}. Manifeste sauvegardé ; relancez plus tard.", file=sys.stderr)
            return 2
        except (ErreurFichier, OSError) as err:
            erreurs += 1
            compteur["erreurs"] += 1
            print(f"ERREUR : {cible_rel} : {err}", file=sys.stderr)
            continue

        rel_final = final.relative_to(racine).as_posix()
        manifeste[cle] = {"statut": "local", "local": rel_final, "taille": e["filesize"],
                          "modifie": e["timemodified"], "sha1": sha}
        enregistrer()
        if statut == "ECRIT":
            compteur["ecrits"] += 1
            print(f"ÉCRIT: {rel_final}")
        else:
            compteur["identiques"] += 1
            print(f"DÉJÀ IDENTIQUE, enregistré : {rel_final}")

    enregistrer()
    print(f"\nRésumé : {compteur['ecrits']} écrit(s), {compteur['adoptes']} adopté(s), "
          f"{compteur['identiques']} identique(s), {compteur['ignores']} ignoré(s), "
          f"{compteur['erreurs']} erreur(s)" + (" [simulation, rien n'a été écrit]" if args.dry_run else ""))
    return 1 if erreurs else 0


def cmd_update(args: argparse.Namespace) -> int:
    racine = resoudre_racine(args.root)
    _entete(racine, "--root" if args.root else "Réglages d'Amphi")
    resultats, manifeste = charger_tout(racine, _afficher_progression)
    seulement = set(args.only or [])
    cibles = [e for e in resultats if e["classe"] == "MAJ_DISPO"
              and (not seulement or e["key"] in seulement)]
    if not cibles:
        print("Aucune mise à jour disponible.")
        return 0
    token = load_token() if not args.dry_run else ""
    erreurs = 0

    for e in cibles:
        cle = e["key"]
        local = racine / e["local"]
        if args.dry_run:
            print(f"[simulation] METTRE À JOUR : {e['local']}  (clé {cle})")
            continue
        try:
            tmp, sha_nouveau = telecharger(e["fileurl"], token, local.parent, e["filesize"],
                                           e["filename"])
            sha_local = sha1_fichier(local)
            if e["sha1"] and sha_local == e["sha1"]:
                # Fichier non modifié par l'élève : remplacement sur place.
                os.replace(tmp, local)
                manifeste[cle] = {"statut": "local", "local": e["local"], "taille": e["filesize"],
                                  "modifie": e["timemodified"], "sha1": sha_nouveau}
                ecrire_manifeste(racine, manifeste)
                print(f"ÉCRIT: {e['local']}")
            else:
                # Fichier modifié localement (ou sha1 inconnu) : nouvelle version à côté.
                etiquette = f"moodle {datetime.fromtimestamp(e['timemodified']).strftime('%d-%m')}" \
                    if e["timemodified"] else "moodle"
                statut, final = installer(tmp, local, sha_nouveau, etiquette)
                manifeste[cle] = {"statut": "local", "local": e["local"], "taille": e["filesize"],
                                  "modifie": e["timemodified"], "sha1": sha_local}
                ecrire_manifeste(racine, manifeste)
                rel_final = final.relative_to(racine).as_posix()
                print(f"CONFLIT : {e['local']} a été modifié localement ; la nouvelle version "
                      f"Moodle est enregistrée à côté.")
                if statut == "ECRIT":
                    print(f"ÉCRIT: {rel_final}")
                else:
                    print(f"DÉJÀ IDENTIQUE, rien écrit : {rel_final}")
        except ErreurReseau as err:
            ecrire_manifeste(racine, manifeste)
            print(f"ERREUR RÉSEAU : {err}. Manifeste sauvegardé ; relancez plus tard.", file=sys.stderr)
            return 2
        except (ErreurFichier, OSError) as err:
            erreurs += 1
            print(f"ERREUR : {e['local']} : {err}", file=sys.stderr)

    if not args.dry_run:
        ecrire_manifeste(racine, manifeste)
    return 1 if erreurs else 0


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(description="Mise à jour des cours locaux depuis Moodle IPSA (campus Paris).")
    sous = parseur.add_subparsers(dest="commande")

    p_plan = sous.add_parser("plan", help="Rapport sans écriture")
    p_plan.add_argument("--json", action="store_true", help="Sortie JSON")
    p_plan.add_argument("--root", help="Racine des cours (défaut : config Amphi)")
    p_plan.add_argument("--where", action="store_true",
                        help="Affiche le dossier Amphi et le dossier des cours résolus, sans contacter Moodle")
    p_plan.set_defaults(func=cmd_plan)

    p_apply = sous.add_parser("apply", help="Enregistre, ignore et télécharge les nouveaux fichiers")
    p_apply.add_argument("--root")
    p_apply.add_argument("--only", nargs="+", metavar="CLE", help="Restreint les téléchargements")
    p_apply.add_argument("--dest", action="append", metavar="CLE=REL_DIR",
                         help="Dossier relatif à la matière pour un nouveau fichier")
    p_apply.add_argument("--ignore", nargs="+", metavar="CLE")
    p_apply.add_argument("--adopt", action="append", metavar="CLE=CHEMIN_REL",
                         help="Rattache un fichier déjà présent localement (chemin relatif au dossier "
                              "de la matière) sans le télécharger")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    p_update = sous.add_parser("update", help="Met à jour les fichiers modifiés sur Moodle")
    p_update.add_argument("--root")
    p_update.add_argument("--only", nargs="+", metavar="CLE")
    p_update.add_argument("--dry-run", action="store_true")
    p_update.set_defaults(func=cmd_update)
    return parseur


def main() -> int:
    argv = sys.argv[1:]
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["plan"] + argv
    args = construire_parseur().parse_args(argv)
    try:
        return args.func(args)
    except ErreurRacine as err:
        print(f"ERREUR : {err}", file=sys.stderr)
        return 2
    except MoodleTokenError as err:
        print(f"ERREUR : {err}", file=sys.stderr)
        print("  Voir le README d'Amphi, section « Étape A — Connexion à Moodle (token) ».",
              file=sys.stderr)
        return 2
    except (MoodleError, ErreurReseau) as err:
        print(f"ERREUR : Moodle injoignable ou réseau indisponible : {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
