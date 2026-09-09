"""Sélection du micro d'entrée.

Par défaut : le micro intégré du ZenBook (« … microphones numériques »), repéré
par correspondance de nom, avec repli sur le périphérique d'entrée par défaut.
"""

from __future__ import annotations

from dataclasses import dataclass

import sounddevice as sd


@dataclass
class InputDevice:
    index: int
    name: str
    channels: int
    default_samplerate: float
    hostapi: str

    @property
    def label(self) -> str:
        return f"{self.name}  ·  {self.hostapi}"


def list_input_devices() -> list[InputDevice]:
    hostapis = sd.query_hostapis()
    out: list[InputDevice] = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        out.append(
            InputDevice(
                index=i,
                name=dev["name"],
                channels=dev["max_input_channels"],
                default_samplerate=dev["default_samplerate"],
                hostapi=hostapis[dev["hostapi"]]["name"],
            )
        )
    return out


def _default_input_index() -> int | None:
    try:
        idx = sd.default.device[0]
        return int(idx) if idx is not None and idx >= 0 else None
    except Exception:
        return None


def pick_microphone(
    name_hint: str = "microphones numériques", override_index: int | None = None
) -> InputDevice:
    """Renvoie le périphérique à utiliser. Lève RuntimeError si aucune entrée."""
    devices = list_input_devices()
    if not devices:
        raise RuntimeError("Aucun périphérique d'entrée audio détecté.")

    by_index = {d.index: d for d in devices}

    if override_index is not None and override_index in by_index:
        return by_index[override_index]

    hint = (name_hint or "").casefold().strip()
    if hint:
        # 1) correspondance sur l'indice, en préférant l'API WASAPI (Windows moderne)
        matches = [d for d in devices if hint in d.name.casefold()]
        if matches:
            matches.sort(key=lambda d: (0 if "wasapi" in d.hostapi.casefold() else 1, d.index))
            return matches[0]

    default_idx = _default_input_index()
    if default_idx in by_index:
        return by_index[default_idx]
    return devices[0]
