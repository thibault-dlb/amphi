"""Synchronisation des devoirs Moodle vers un calendrier iCloud (CalDAV).

Seuls les événements dont l'UID commence par ``UID_PREFIX`` sont gérés ; le
calendrier n'est jamais vidé en dehors de ces événements. Le mot de passe
spécifique à l'app n'est jamais journalisé.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import icalendar
from caldav import DAVClient
from caldav.calendarobjectresource import Event
from caldav.lib.error import AuthorizationError, DAVError, NotFoundError, ReportError
from niquests.exceptions import RequestException   # caldav 3.x passe par niquests, pas requests

from amphi.moodle.model import UID_PREFIX, EventInfo, EventSpec

log = logging.getLogger(__name__)

CALDAV_URL = "https://caldav.icloud.com"
TZ = ZoneInfo("Europe/Paris")
PRODID = "-//Amphi//Moodle IPSA//FR"
HASH_PROP = "X-AMPHI-HASH"


class ICloudError(Exception):
    """Erreur de communication avec iCloud (réseau, CalDAV...)."""


class ICloudAuthError(ICloudError):
    """Identifiants iCloud refusés."""


# --------------------------------------------------------------------------
# Fonctions pures : construction et lecture de l'iCalendar (testables hors ligne)
# --------------------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    """Force un datetime en aware ; un datetime naïf est considéré comme Europe/Paris."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt


def build_ical(spec: EventSpec) -> str:
    """Construit le VCALENDAR (texte) d'un événement Amphi, avec VTIMEZONE Europe/Paris."""
    start = _aware(spec.start).astimezone(TZ)
    end = _aware(spec.end).astimezone(TZ)

    cal = icalendar.Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")

    ev = icalendar.Event()
    ev.add("uid", spec.uid)
    ev.add("dtstamp", datetime.now(timezone.utc))
    ev.add("dtstart", start)
    ev.add("dtend", end)
    ev.add("summary", spec.summary)
    ev.add("description", spec.description)
    if spec.url:
        ev.add("url", spec.url)
    ev.add("transp", "OPAQUE")
    ev.add(HASH_PROP, spec.hash)

    for delai in spec.alarms:
        alarm = icalendar.Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", spec.summary)
        # Délai positif avant l'échéance -> déclencheur négatif relatif à la FIN.
        alarm.add("trigger", -delai, parameters={"RELATED": "END"})
        ev.add_component(alarm)

    cal.add_component(ev)
    # VTIMEZONE limitée à une fenêtre autour de l'événement (évite la plage 1970-2038 complète).
    cal.add_missing_timezones(
        first_date=(start - timedelta(days=366)).date(),
        last_date=(end + timedelta(days=366)).date(),
    )
    return cal.to_ical().decode("utf-8")


def _to_paris(value: Any) -> datetime | None:
    """Convertit une valeur DTEND (datetime, date ou None) en datetime aware Europe/Paris."""
    if value is None:
        return None
    dt = value.dt
    if not isinstance(dt, datetime):
        # Événement « journée entière » : on prend minuit local.
        dt = datetime(dt.year, dt.month, dt.day)
    return _aware(dt).astimezone(TZ)


def parse_event_info(ical_text: str) -> EventInfo | None:
    """Lit un iCalendar renvoyé par le serveur ; None si ce n'est pas un événement Amphi."""
    try:
        cal = icalendar.Calendar.from_ical(ical_text)
    except ValueError:
        log.warning("iCalendar illisible renvoyé par iCloud, ignoré.")
        return None

    for comp in cal.walk("VEVENT"):
        uid = str(comp.get("UID", ""))
        if not uid.startswith(UID_PREFIX):
            return None
        hash_ = str(comp.get(HASH_PROP, ""))
        end = _to_paris(comp.get("DTEND"))
        return EventInfo(uid=uid, hash=hash_, end=end)
    return None


# --------------------------------------------------------------------------
# Accès CalDAV
# --------------------------------------------------------------------------

@contextmanager
def _traduire_erreurs(action: str) -> Iterator[None]:
    """Convertit les erreurs caldav / réseau en ICloudError (ou ICloudAuthError)."""
    try:
        yield
    except ICloudError:
        raise
    except AuthorizationError as exc:
        raise ICloudAuthError(
            "Identifiants iCloud refusés : vérifiez l'identifiant Apple et "
            "le mot de passe spécifique à l'app (xxxx-xxxx-xxxx-xxxx)."
        ) from exc
    except RequestException as exc:
        raise ICloudError(
            f"Connexion à iCloud impossible pendant {action} ({type(exc).__name__})."
        ) from exc
    except DAVError as exc:
        raise ICloudError(f"Erreur CalDAV iCloud pendant {action} : {exc}") from exc


def _nom_calendrier(cal: Any) -> str | None:
    try:
        return cal.get_display_name()
    except DAVError:
        return None


class ICloudCalendar:
    """Calendrier iCloud dédié aux devoirs Moodle."""

    def __init__(self, apple_id: str, password: str, calendar_name: str = "Moodle IPSA") -> None:
        self._apple_id = apple_id
        self._password = password
        self._calendar_name = calendar_name
        self._calendar: Any = None
        # uid -> objet Event caldav (pour mise à jour / suppression sans nouvelle recherche)
        self._events: dict[str, Any] = {}
        self._liste_chargee = False     # True après list_events() : _events fait alors foi

    # ---- connexion -------------------------------------------------------

    def connect(self) -> None:
        """Se connecte à iCloud et récupère (ou crée) le calendrier. Idempotent."""
        if self._calendar is not None:
            return
        with _traduire_erreurs("la connexion"):
            client = DAVClient(
                url=CALDAV_URL,
                username=self._apple_id,
                password=self._password,
            )
            principal = client.get_principal()
            calendrier = None
            for cal in principal.get_calendars():
                if _nom_calendrier(cal) == self._calendar_name:
                    calendrier = cal
                    break
            if calendrier is None:
                log.info("Création du calendrier iCloud « %s »", self._calendar_name)
                calendrier = principal.make_calendar(
                    name=self._calendar_name,
                    supported_calendar_component_set=["VEVENT"],
                )
            self._calendar = calendrier

    def _ensure_connected(self) -> None:
        if self._calendar is None:
            self.connect()

    def _trouver(self, uid: str) -> Any | None:
        """Renvoie l'objet caldav de cet UID, ou None s'il n'existe pas."""
        if uid in self._events:
            return self._events[uid]
        if self._liste_chargee:
            # Le calendrier a déjà été lu en entier : inutile (et refusé par iCloud,
            # qui répond 412 aux recherches REPORT par UID) d'interroger le serveur.
            return None
        try:
            obj = self._calendar.get_object_by_uid(uid, comp_class=Event)
        except (NotFoundError, ReportError):
            return None
        self._events[uid] = obj
        return obj

    # ---- API publique ----------------------------------------------------

    def list_events(self) -> dict[str, EventInfo]:
        """Tous les événements Amphi du calendrier, indexés par UID."""
        self._ensure_connected()
        with _traduire_erreurs("la lecture des événements"):
            objets = self._calendar.get_events()
            resultat: dict[str, EventInfo] = {}
            self._events = {}
            for obj in objets:
                info = parse_event_info(obj.get_data())
                if info is None:
                    continue
                resultat[info.uid] = info
                self._events[info.uid] = obj
            self._liste_chargee = True
        return resultat

    def put_event(self, spec: EventSpec) -> None:
        """Crée ou met à jour l'événement de cet UID (jamais de doublon)."""
        self._ensure_connected()
        ical = build_ical(spec)
        with _traduire_erreurs("l'écriture de l'événement"):
            obj = self._trouver(spec.uid)
            if obj is not None:
                obj.data = ical
                obj.save()
                log.debug("Événement mis à jour : %s", spec.uid)
            else:
                obj = self._calendar.add_event(ical)
                log.debug("Événement créé : %s", spec.uid)
            self._events[spec.uid] = obj

    def delete_event(self, uid: str) -> None:
        """Supprime l'événement s'il existe ; sans erreur sinon."""
        self._ensure_connected()
        with _traduire_erreurs("la suppression de l'événement"):
            obj = self._trouver(uid)
            if obj is None:
                self._events.pop(uid, None)
                return
            obj.delete()
            self._events.pop(uid, None)


def test_connection(apple_id: str, password: str, calendar_name: str = "Moodle IPSA") -> str:
    """Vérifie la connexion (crée le calendrier si besoin) et renvoie un message français."""
    cal = ICloudCalendar(apple_id, password, calendar_name)
    cal.connect()
    nombre = len(cal.list_events())
    return (
        f"Connexion iCloud OK — calendrier « {calendar_name} » prêt "
        f"({nombre} événements Amphi)."
    )
