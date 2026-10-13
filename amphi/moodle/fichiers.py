"""Synchronisation des fichiers Moodle (campus Paris) vers les dossiers de cours locaux.

Logique sans interface : classification des fichiers Moodle par rapport au disque et
au manifeste (<racine>/.moodle_sync.json), téléchargement et installation. Utilisé par
la CLI /maj-moodle et par la synchro Amphi (`documents_a_recuperer`).

Côté Moodle, seules des fonctions de LECTURE sont appelées. Le token n'est jamais
affiché ni journalisé. `documents_a_recuperer` n'écrit jamais dans le manifeste.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from ..config import Config
from .client import MoodleClient, _cmids_autorises, load_token, matiere_from_fullname

NOM_MANIFESTE = ".moodle_sync.json"
DOSSIERS_IGNORES = {"Enregistrements", ".venv", ".git", ".claude"}
MODNAMES_FICHIERS = ("resource", "folder")
TIMEOUT_TELECHARGEMENT = 120.0

_ANNEE = re.compile(r"^(\d{2})-")
_CARACTERES_INTERDITS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TP_TD_NUMERO = re.compile(r"(?<![a-z])(tp|td)\s*[-_ ]?\s*(\d+)")
_MOT_TP = re.compile(r"(?<![a-z0-9])(tp|tps)(?![a-z0-9])")
_MOT_TD = re.compile(r"(?<![a-z0-9])(td|tds)(?![a-z0-9])")
_MOT_DM = re.compile(r"(?<![a-z0-9])dm(?![a-z])")
_DOSSIER_TP_NUMEROTE = re.compile(r"TP\d+", re.IGNORECASE)
_MOTS_SIGNIFICATIFS = re.compile(r"[a-z0-9]{3,}")

CLASSES_ORDRE = ["NOUVEAU", "MAJ_DISPO", "MANQUANT_LOCAL", "DEJA_PRESENT", "DEJA_PRESENT_RENOMME"]
# Classes qui demandent une action de l'utilisateur (documents à récupérer).
CLASSES_A_RECUPERER = ("NOUVEAU", "MAJ_DISPO", "MANQUANT_LOCAL")
TITRES_CLASSES = {
    "NOUVEAU": "À TÉLÉCHARGER (nouveaux fichiers)",
    "MAJ_DISPO": "MISE À JOUR DISPONIBLE (version Moodle plus récente)",
    "MANQUANT_LOCAL": "MANQUANTS EN LOCAL (enregistrés mais absents du dossier)",
    "DEJA_PRESENT": "DÉJÀ PRÉSENTS, MÊME NOM (à enregistrer avec apply)",
    "DEJA_PRESENT_RENOMME": "DÉJÀ PRÉSENTS SOUS UN AUTRE NOM, MÊME TAILLE (à vérifier puis enregistrer avec apply)",
}


class ErreurReseau(Exception):
    """Connexion coupée pendant un téléchargement : la commande s'arrête (code 2)."""


class ErreurFichier(Exception):
    """Échec sur un fichier précis : on passe au suivant (code 1 en fin de run)."""


# ---------------------------------------------------------------------------
# Outils de texte
# ---------------------------------------------------------------------------

def normaliser(texte: str) -> str:
    """Minuscules, sans accents."""
    decompose = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


def taille_lisible(octets: int) -> str:
    """Formate une taille en Ko ou Mo (virgule française)."""
    if octets >= 1024 * 1024:
        return f"{octets / (1024 * 1024):.1f} Mo".replace(".", ",")
    kilo = octets / 1024
    return (f"{kilo:.0f} Ko" if kilo >= 10 else f"{kilo:.1f} Ko").replace(".", ",")


def date_lisible(horodatage: int) -> str:
    """Horodatage Unix → « JJ/MM/AAAA HH:MM »."""
    if not horodatage:
        return "date inconnue"
    return datetime.fromtimestamp(horodatage).strftime("%d/%m/%Y %H:%M")


def nettoyer_nom(nom: str) -> str:
    """Remplace les caractères interdits Windows, sans toucher aux accents ni à l'extension."""
    return _CARACTERES_INTERDITS.sub("_", nom).strip() or "fichier"


def ressemblance(a: str, b: str) -> tuple[float, int]:
    """(similarité 0..1 entre noms compacts, nombre de mots communs de 3 lettres ou plus)."""
    na, nb = normaliser(a), normaliser(b)
    ratio = difflib.SequenceMatcher(None, re.sub(r"[^a-z0-9]", "", na),
                                    re.sub(r"[^a-z0-9]", "", nb)).ratio()
    communs = len(set(_MOTS_SIGNIFICATIFS.findall(na)) & set(_MOTS_SIGNIFICATIFS.findall(nb)))
    return ratio, communs


def sha1_fichier(chemin: Path) -> str:
    """SHA-1 d'un fichier local, lu par blocs."""
    h = hashlib.sha1()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Destination suggérée (dans le dossier de la matière)
# ---------------------------------------------------------------------------

def dossier_tp_plat(dossier_matiere: Path | None) -> bool:
    """Vrai si le dossier TP/ de la matière contient des fichiers à plat et aucun TP<n>/.
    Cas des TP cumulatifs (ex. « Bases des données avancées »)."""
    if dossier_matiere is None or not dossier_matiere.is_dir():
        return False
    tp = next((p for p in dossier_matiere.iterdir()
               if p.is_dir() and p.name.casefold() == "tp"), None)
    if tp is None:
        return False
    entrees = list(tp.iterdir())
    a_des_fichiers = any(p.is_file() for p in entrees)
    a_des_sous_numerotes = any(p.is_dir() and _DOSSIER_TP_NUMEROTE.fullmatch(p.name)
                               for p in entrees)
    return a_des_fichiers and not a_des_sous_numerotes


def suggerer_destination(section: str, module: str, nom_fichier: str,
                         dossier_matiere: Path | None) -> tuple[str, str, bool]:
    """Renvoie (dossier relatif à la matière « TP/TP3/ », raison, à_répartir).

    Ordre : numéro TP/TD (module puis nom), TP/TD sans numéro, projet, DM,
    annales/examens, cours, sinon « À classer/ »."""
    mod = normaliser(module)
    fn = normaliser(nom_fichier)
    sec = normaliser(section)

    for source, texte in (("module", mod), ("nom du fichier", fn)):
        m = _TP_TD_NUMERO.search(texte)
        if m:
            genre, numero = m.group(1).upper(), int(m.group(2))
            if genre == "TD":
                return f"TD/TD{numero}/", f"numéro TD{numero} dans le {source}", False
            if dossier_tp_plat(dossier_matiere):
                return "TP/", f"TP{numero} : dossier TP/ à plat (TP cumulatifs)", False
            return f"TP/TP{numero}/", f"numéro TP{numero} dans le {source}", False

    if _MOT_TP.search(mod) or _MOT_TP.search(fn):
        return "TP/", "TP sans numéro : à répartir", True
    if _MOT_TD.search(mod) or _MOT_TD.search(fn):
        return "TD/", "TD sans numéro : à répartir", True
    if "projet" in mod or "project" in mod or "projet" in fn or "project" in fn:
        return "Projet/", "mot « projet » dans le module ou le nom", False
    if _MOT_DM.search(mod) or fn.startswith("dm") or "-dm" in fn:
        return "DM/", "« DM » dans le module ou le nom", False
    if any(w in mod or w in fn or w in sec for w in ("annale", "exam")):
        return "Révisions/", "annales ou examens dans la section, le module ou le nom", False
    if any(w in mod or w in fn for w in ("cours", "course", "chapitre", "slides")):
        return "Cours/", "mot « cours » ou équivalent dans le module ou le nom", False
    return "À classer/", "aucun indice reconnu", False


def cible_relative(matiere: str, dest_dir: str, nom_fichier: str) -> str:
    """Chemin relatif à la racine : « Matière/dossier/fichier »."""
    return f"{matiere}/{dest_dir}{nettoyer_nom(nom_fichier)}"


# ---------------------------------------------------------------------------
# Moodle (lecture seule)
# ---------------------------------------------------------------------------

def charger_fichiers_moodle(client: MoodleClient, progression) -> list[dict[str, Any]]:
    """Liste les fichiers (resource / folder) des cours de l'année courante, campus Paris."""
    progression("Moodle : connexion…")
    site = client.call("core_webservice_get_site_info")
    cours = client.call("core_enrol_get_users_courses", userid=site["userid"])

    annees: dict[int, int] = {}
    for c in cours:
        m = _ANNEE.match(c.get("fullname") or "")
        if m:
            annees[c["id"]] = int(m.group(1))
    if not annees:
        return []
    annee_max = max(annees.values())
    retenus = [c for c in cours if annees.get(c["id"]) == annee_max]

    fichiers: list[dict[str, Any]] = []
    for i, c in enumerate(retenus, 1):
        progression(f"Moodle : contenu des cours ({i}/{len(retenus)})…")
        nom_cours = c.get("fullname") or ""
        contenu = client.call("core_course_get_contents", courseid=c["id"])
        autorises = _cmids_autorises(contenu)
        for section in contenu:
            for module in section.get("modules") or []:
                if module.get("id") not in autorises or module.get("modname") not in MODNAMES_FICHIERS:
                    continue
                for entree in module.get("contents") or []:
                    if entree.get("type") != "file":
                        continue
                    filepath = entree.get("filepath") or "/"
                    filename = entree.get("filename") or ""
                    fichiers.append({
                        "key": f"{c['id']}/{module['id']}{filepath}{filename}",
                        "courseid": c["id"],
                        "cours": nom_cours,
                        "matiere": matiere_from_fullname(nom_cours),
                        "section": section.get("name") or "",
                        "module": module.get("name") or "",
                        "modname": module.get("modname"),
                        "cmid": module["id"],
                        "filename": filename,
                        "filesize": int(entree.get("filesize") or 0),
                        "timemodified": int(entree.get("timemodified") or 0),
                        "fileurl": entree.get("fileurl") or "",
                    })
    return fichiers


# ---------------------------------------------------------------------------
# Manifeste
# ---------------------------------------------------------------------------

def charger_manifeste(racine: Path) -> dict[str, dict[str, Any]]:
    """Lit <racine>/.moodle_sync.json (vide si absent)."""
    chemin = racine / NOM_MANIFESTE
    if not chemin.exists():
        return {}
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    return dict(donnees.get("files") or {})


def ecrire_manifeste(racine: Path, fichiers: dict[str, dict[str, Any]]) -> None:
    """Écriture atomique (fichier temporaire puis remplacement)."""
    chemin = racine / NOM_MANIFESTE
    tmp = racine / (NOM_MANIFESTE + ".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "files": fichiers}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, chemin)


# ---------------------------------------------------------------------------
# Inventaire local et classification
# ---------------------------------------------------------------------------

def lister_locaux(dossier: Path, racine: Path) -> list[dict[str, Any]]:
    """Fichiers d'un dossier de matière, récursivement, sans les dossiers ignorés."""
    resultats: list[dict[str, Any]] = []
    if not dossier.is_dir():
        return resultats
    for dirpath, dirnames, filenames in os.walk(dossier):
        dirnames[:] = [d for d in dirnames if d not in DOSSIERS_IGNORES]
        for nom in filenames:
            if nom.startswith(".moodle_"):
                continue
            chemin = Path(dirpath) / nom
            try:
                taille = chemin.stat().st_size
            except OSError:
                continue
            resultats.append({
                "rel": chemin.relative_to(racine).as_posix(),
                "name": nom,
                "size": taille,
            })
    return resultats


def classer(fichiers: list[dict[str, Any]], manifeste: dict[str, dict[str, Any]],
            racine: Path) -> list[dict[str, Any]]:
    """Attribue une classe à chaque fichier Moodle (voir la spécification du manifeste)."""
    cache: dict[str, list[dict[str, Any]]] = {}

    def locaux(matiere: str) -> list[dict[str, Any]]:
        if matiere not in cache:
            cache[matiere] = lister_locaux(racine / matiere, racine)
        return cache[matiere]

    # Fichiers locaux déjà rattachés à une autre clé du manifeste : jamais candidats.
    reserves = {m["local"] for m in manifeste.values()
                if m.get("statut") == "local" and m.get("local")}
    revendiques: set[str] = set()
    resultats: list[dict[str, Any]] = []

    for f in fichiers:
        e = dict(f, local=None, cible=None, dest_dir=None, raison="", a_repartir=False,
                 sha1="", nom_proche=None, similarite=None, mots_communs=0, attention="")
        m = manifeste.get(f["key"])
        matiere = f["matiere"]

        if m and m.get("statut") == "ignore":
            e["classe"] = "IGNORE"
        elif m and m.get("statut") == "local" and m.get("local"):
            loc = m["local"]
            e["local"] = loc
            e["sha1"] = m.get("sha1") or ""
            if (racine / loc).is_file():
                modifie_moodle = f["timemodified"] > int(m.get("modifie") or 0)
                if modifie_moodle and f["filesize"] != int(m.get("taille") or 0):
                    e["classe"] = "MAJ_DISPO"
                else:
                    e["classe"] = "OK"
            else:
                e["classe"] = "MANQUANT_LOCAL"
                e["cible"] = loc
            revendiques.add(loc)
        else:
            e = _classer_inconnu(e, f, locaux(matiere), reserves, revendiques, racine,
                                 dossier=racine / matiere)
        resultats.append(e)
    return resultats


def _classer_inconnu(e: dict[str, Any], f: dict[str, Any], candidats: list[dict[str, Any]],
                     reserves: set[str], revendiques: set[str], racine: Path,
                     dossier: Path) -> dict[str, Any]:
    """Fichier absent du manifeste : recherche d'un fichier local identique, sinon nouveau."""
    libres = [c for c in candidats if c["rel"] not in reserves | revendiques]
    nom = f["filename"].casefold()

    memes_noms = [c for c in libres if c["name"].casefold() == nom]
    if memes_noms:
        memes_noms.sort(key=lambda c: c["size"] != f["filesize"])
        choisi = memes_noms[0]
        e["classe"] = "DEJA_PRESENT"
        e["local"] = choisi["rel"]
        if choisi["size"] != f["filesize"]:
            e["attention"] = (f"taille différente de Moodle (locale {choisi['size']} o, "
                              f"Moodle {f['filesize']} o) : vérifier")
    else:
        memes_tailles = [c for c in libres if c["size"] == f["filesize"]]
        if memes_tailles:
            def score(c: dict[str, Any]) -> tuple[int, float]:
                ratio, communs = ressemblance(f["filename"], c["name"])
                return communs, ratio
            choisi = max(memes_tailles, key=score)
            ratio, communs = ressemblance(f["filename"], choisi["name"])
            e["classe"] = "DEJA_PRESENT_RENOMME"
            e["local"] = choisi["rel"]
            e["nom_proche"] = choisi["name"]
            e["similarite"] = ratio
            e["mots_communs"] = communs
        else:
            e["classe"] = "NOUVEAU"
            dest, raison, repartir = suggerer_destination(
                f["section"], f["module"], f["filename"], dossier)
            e["dest_dir"] = dest
            e["raison"] = raison
            e["a_repartir"] = repartir
            e["cible"] = cible_relative(f["matiere"], dest, f["filename"])
            return e

    if e["local"]:
        revendiques.add(e["local"])
    return e


# ---------------------------------------------------------------------------
# Téléchargement et installation
# ---------------------------------------------------------------------------

def telecharger(fileurl: str, token: str, dossier: Path, taille_attendue: int,
                nom_indicatif: str) -> tuple[Path, str]:
    """Télécharge dans un fichier temporaire du dossier cible.

    Renvoie (chemin temporaire, sha1). Vérifie la taille. Le token n'apparaît
    dans aucun message d'erreur."""
    dossier.mkdir(parents=True, exist_ok=True)
    descripteur, nom_tmp = tempfile.mkstemp(dir=dossier, prefix=".moodle_tmp_", suffix=".part")
    os.close(descripteur)
    tmp = Path(nom_tmp)
    sep = "&" if "?" in fileurl else "?"
    url = f"{fileurl}{sep}token={token}"
    total = 0
    h = hashlib.sha1()
    try:
        with httpx.Client(timeout=TIMEOUT_TELECHARGEMENT, follow_redirects=True) as http:
            with http.stream("GET", url) as reponse:
                if reponse.status_code >= 400:
                    raise ErreurFichier(f"{nom_indicatif} : Moodle a répondu HTTP {reponse.status_code}")
                type_contenu = reponse.headers.get("content-type", "")
                if type_contenu.startswith("text/html") and not nom_indicatif.lower().endswith(
                        (".htm", ".html")):
                    raise ErreurFichier(f"{nom_indicatif} : page HTML reçue au lieu du fichier "
                                        "(droits ou token ?)")
                with open(tmp, "wb") as f:
                    for morceau in reponse.iter_bytes():
                        f.write(morceau)
                        h.update(morceau)
                        total += len(morceau)
        if total != taille_attendue:
            raise ErreurFichier(f"{nom_indicatif} : taille reçue {total} o, attendue {taille_attendue} o")
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise ErreurReseau(f"connexion interrompue pendant « {nom_indicatif} » "
                           f"({type(e).__name__})") from None
    except ErreurFichier:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, h.hexdigest()


def installer(tmp: Path, cible: Path, sha_nouveau: str, etiquette: str) -> tuple[str, Path]:
    """Place le fichier téléchargé sur sa cible sans jamais écraser un fichier différent.

    Renvoie (« ECRIT » | « IDENTIQUE », chemin final)."""
    if not cible.exists():
        os.replace(tmp, cible)
        return "ECRIT", cible
    if sha1_fichier(cible) == sha_nouveau:
        tmp.unlink(missing_ok=True)
        return "IDENTIQUE", cible
    numero = 1
    while True:
        suffixe = f" ({etiquette})" if numero == 1 else f" ({etiquette} {numero})"
        alt = cible.with_name(f"{cible.stem}{suffixe}{cible.suffix}")
        if not alt.exists():
            os.replace(tmp, alt)
            return "ECRIT", alt
        if sha1_fichier(alt) == sha_nouveau:
            tmp.unlink(missing_ok=True)
            return "IDENTIQUE", alt
        numero += 1


# ---------------------------------------------------------------------------
# Racine, configuration et chargement global
# ---------------------------------------------------------------------------

class ErreurRacine(Exception):
    """Dossier des cours introuvable (la commande s'arrête, code 2)."""


def racine_des_cours(arg_root: str | None) -> tuple[Path, str]:
    """(racine, origine) sans vérifier son existence. Origine : « --root » ou « Réglages d'Amphi »."""
    if arg_root:
        return Path(arg_root).expanduser().resolve(), "--root"
    cfg = Config.load()
    return (cfg.courses_root_path() / cfg.niveau).resolve(), "Réglages d'Amphi"


def resoudre_racine(arg_root: str | None) -> Path:
    """Racine des cours : --root, sinon <courses_root>/<niveau> de la config Amphi."""
    racine, origine = racine_des_cours(arg_root)
    if not racine.is_dir():
        if origine == "--root":
            raise ErreurRacine(f"dossier racine introuvable : {racine}")
        raise ErreurRacine("le dossier des cours défini dans les Réglages d'Amphi n'existe pas : "
                           f"{racine}")
    return racine


def base_url() -> str:
    return Config.load().moodle.base_url


def charger_tout(racine: Path, progression: Callable[[str], None] | None = None,
                 url_moodle: str | None = None) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Charge les fichiers Moodle et classe tout le monde. Ne touche pas au disque."""
    manifeste = charger_manifeste(racine)
    with MoodleClient(url_moodle or base_url(), load_token()) as client:
        fichiers = charger_fichiers_moodle(client, progression or (lambda _m: None))
    resultats = classer(fichiers, manifeste, racine)
    return resultats, manifeste


def _dossier_relatif(matiere: str, rel: str) -> str:
    """« Matière/TP/TP0/fichier.pdf » → « TP/TP0/ » (relatif à la matière)."""
    rel = rel.replace("\\", "/")
    prefixe = f"{matiere}/"
    if rel.startswith(prefixe):
        rel = rel[len(prefixe):]
    return rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""


def documents_a_recuperer(cfg=None, racine: Path | None = None,
                          progression: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
    """Fichiers Moodle à récupérer : NOUVEAU, MANQUANT_LOCAL ou MAJ_DISPO.

    Même classification que « plan ». Lecture seule : le manifeste n'est jamais écrit.
    Racine = `racine`, sinon <courses_root>/<niveau> de la config (Config.load() si
    `cfg` est absent). Lève ErreurRacine si la racine n'existe pas.
    """
    if cfg is None:
        cfg = Config.load()
    if racine is None:
        racine = cfg.courses_root_path() / cfg.niveau
        if not Path(racine).is_dir():
            raise ErreurRacine("le dossier des cours défini dans les Réglages d'Amphi n'existe pas : "
                               f"{racine}")
    racine = Path(racine).resolve()
    if not racine.is_dir():
        raise ErreurRacine(f"dossier racine introuvable : {racine}")

    resultats, _ = charger_tout(racine, progression, cfg.moodle.base_url)
    documents: list[dict[str, Any]] = []
    for e in resultats:
        if e["classe"] not in CLASSES_A_RECUPERER:
            continue
        if e["classe"] == "NOUVEAU":
            dest = e["dest_dir"]
        else:
            dest = _dossier_relatif(e["matiere"], e["local"] or e["cible"])
        documents.append({
            "cle": e["key"],
            "statut": e["classe"],
            "matiere": e["matiere"],
            "nom": e["filename"],
            "taille": e["filesize"],
            "section": e["section"],
            "module": e["module"],
            "dest": dest,
            "cible": e["cible"],
            "local": e["local"],
            "timemodified": e["timemodified"],
        })
    return documents
