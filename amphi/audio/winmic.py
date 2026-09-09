"""État muet / volume du micro par défaut (Windows Core Audio, via ctypes COM).

Sert à prévenir l'utilisateur AVANT d'enregistrer si le micro est coupé — le cas
classique où l'on enregistre 1 h de silence sans s'en rendre compte. Tout échoue en
douceur (renvoie None) : c'est un confort, pas un point critique.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import POINTER, byref, c_float, c_int, c_void_p
from ctypes.wintypes import BOOL, DWORD, LPCWSTR

log = logging.getLogger(__name__)

try:  # pragma: no cover - Windows uniquement
    _ole32 = ctypes.oledll.ole32
except Exception:  # noqa: BLE001
    _ole32 = None

CLSCTX_ALL = 0x17
_EDATA_CAPTURE = 1
_ROLE_CONSOLE = 0


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_byte * 8),
    ]

    def __init__(self, s: str) -> None:
        super().__init__()
        _ole32.CLSIDFromString(LPCWSTR(s), byref(self))


_CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"


def _vtbl_call(obj: c_void_p, index: int, restype, *argtypes):
    vtbl = ctypes.cast(obj, POINTER(c_void_p))[0]
    fn_ptr = ctypes.cast(vtbl, POINTER(c_void_p))[index]
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(fn_ptr)


def _release(obj: c_void_p) -> None:
    try:
        _vtbl_call(obj, 2, c_int)(obj)  # IUnknown::Release
    except Exception:  # noqa: BLE001
        pass


def _endpoint_volume() -> c_void_p | None:
    """Renvoie un IAudioEndpointVolume* sur le micro par défaut, ou None."""
    if _ole32 is None:
        return None
    try:
        _ole32.CoInitialize(None)
    except Exception:  # noqa: BLE001
        pass
    enum = c_void_p()
    try:
        _ole32.CoCreateInstance(
            byref(_GUID(_CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
            byref(_GUID(_IID_IMMDeviceEnumerator)), byref(enum),
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("CoCreateInstance MMDeviceEnumerator: %s", exc)
        return None

    device = c_void_p()
    try:
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint (vtable index 4)
        hr = _vtbl_call(enum, 4, c_int, DWORD, DWORD, POINTER(c_void_p))(
            enum, _EDATA_CAPTURE, _ROLE_CONSOLE, byref(device)
        )
        if hr != 0 or not device:
            return None
        vol = c_void_p()
        # IMMDevice::Activate (vtable index 3)
        hr = _vtbl_call(device, 3, c_int, POINTER(_GUID), DWORD, c_void_p, POINTER(c_void_p))(
            device, byref(_GUID(_IID_IAudioEndpointVolume)), CLSCTX_ALL, None, byref(vol)
        )
        if hr != 0 or not vol:
            return None
        return vol
    finally:
        if device:
            _release(device)
        _release(enum)


# IAudioEndpointVolume vtable : IUnknown(0-2), RegisterControlChangeNotify(3),
# UnregisterControlChangeNotify(4), GetChannelCount(5), SetMasterVolumeLevel(6),
# SetMasterVolumeLevelScalar(7), GetMasterVolumeLevel(8), GetMasterVolumeLevelScalar(9),
# ... SetMute(14), GetMute(15)
_GET_MUTE = 15
_SET_MUTE = 14
_GET_SCALAR = 9


def is_muted() -> bool | None:
    vol = _endpoint_volume()
    if vol is None:
        return None
    try:
        muted = BOOL()
        hr = _vtbl_call(vol, _GET_MUTE, c_int, POINTER(BOOL))(vol, byref(muted))
        return bool(muted.value) if hr == 0 else None
    finally:
        _release(vol)


def level_percent() -> int | None:
    vol = _endpoint_volume()
    if vol is None:
        return None
    try:
        scalar = c_float()
        hr = _vtbl_call(vol, _GET_SCALAR, c_int, POINTER(c_float))(vol, byref(scalar))
        return round(scalar.value * 100) if hr == 0 else None
    finally:
        _release(vol)


def set_muted(muted: bool) -> bool:
    vol = _endpoint_volume()
    if vol is None:
        return False
    try:
        null_guid = _GUID("{00000000-0000-0000-0000-000000000000}")
        hr = _vtbl_call(vol, _SET_MUTE, c_int, BOOL, POINTER(_GUID))(
            vol, BOOL(1 if muted else 0), byref(null_guid)
        )
        return hr == 0
    finally:
        _release(vol)
