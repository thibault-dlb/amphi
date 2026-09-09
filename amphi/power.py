"""Alimentation secteur et inhibition de la veille Windows (ctypes, sans dépendance)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wintypes.BYTE),
        ("BatteryFlag", wintypes.BYTE),
        ("BatteryLifePercent", wintypes.BYTE),
        ("SystemStatusFlag", wintypes.BYTE),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


_kernel32.GetSystemPowerStatus.argtypes = [ctypes.POINTER(_SYSTEM_POWER_STATUS)]
_kernel32.GetSystemPowerStatus.restype = wintypes.BOOL
_kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
_kernel32.SetThreadExecutionState.restype = wintypes.DWORD


def on_ac_power() -> bool:
    """True si branché au secteur. En cas de doute (desktop, API muette) : True."""
    status = _SYSTEM_POWER_STATUS()
    if not _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return True
    ac = status.ACLineStatus & 0xFF
    if ac == 255:            # inconnu -> ne pas bloquer la transcription
        return True
    return ac == 1


def battery_percent() -> int | None:
    status = _SYSTEM_POWER_STATUS()
    if not _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    pct = status.BatteryLifePercent & 0xFF
    return None if pct == 255 else pct


def inhibit_sleep(keep_display_on: bool = True) -> None:
    """Empêche la mise en veille tant que release_sleep() n'est pas appelé.

    L'état est lié au thread appelant : appeler depuis le thread principal Qt,
    qui vit toute la durée de l'application.
    """
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if keep_display_on:
        flags |= ES_DISPLAY_REQUIRED
    _kernel32.SetThreadExecutionState(flags)


def release_sleep() -> None:
    _kernel32.SetThreadExecutionState(ES_CONTINUOUS)
