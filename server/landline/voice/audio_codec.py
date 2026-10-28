"""μ-law and PCM conversion for Gemini Live.

Twilio frames are G.711 μ-law at 8 kHz and the browser is PCM16 at 24 kHz. Gemini Live takes PCM16
(native 16 kHz, other rates if the MIME type says so) and always speaks PCM16 at 24 kHz. The bridge
keeps seeing the transport's own format; this module converts at the session edge. `audioop` is in
the standard library on the 3.12 we run.
"""
from __future__ import annotations

import audioop
import base64

GEMINI_IN_RATE = 16000
GEMINI_OUT_RATE = 24000
PHONE_RATE = 8000
WIDTH = 2


class Transcoder:
    """One call's audio, both ways. Rate conversion keeps its state so chunk edges don't click."""

    def __init__(self, audio_format: dict) -> None:
        self.format = audio_format
        self._to_state = None
        self._from_state = None

    @property
    def pcmu(self) -> bool:
        return self.format.get("type") == "audio/pcmu"

    @property
    def pcm_rate(self) -> int:
        return int(self.format.get("rate") or GEMINI_OUT_RATE)

    def to_gemini(self, b64: str) -> tuple[bytes, int] | None:
        """Transport audio (base64) to PCM16 for Gemini, and the sample rate the MIME type must name."""
        raw = base64.b64decode(b64 or "")
        if not raw:
            return None
        if self.pcmu:
            pcm, self._to_state = audioop.ratecv(
                audioop.ulaw2lin(raw, WIDTH), WIDTH, 1, PHONE_RATE, GEMINI_IN_RATE, self._to_state)
            return _even(pcm), GEMINI_IN_RATE
        rate = self.pcm_rate
        return _even(raw), rate

    def from_gemini(self, pcm: bytes) -> str:
        """Gemini's PCM16 24 kHz to base64 in the transport's format. Empty when the chunk is too short."""
        pcm = _even(pcm)
        if not pcm:
            return ""
        if self.pcmu:
            pcm, self._from_state = audioop.ratecv(
                pcm, WIDTH, 1, GEMINI_OUT_RATE, PHONE_RATE, self._from_state)
            if not pcm:
                return ""
            return base64.b64encode(audioop.lin2ulaw(pcm, WIDTH)).decode()
        if self.pcm_rate != GEMINI_OUT_RATE:
            pcm, self._from_state = audioop.ratecv(
                pcm, WIDTH, 1, GEMINI_OUT_RATE, self.pcm_rate, self._from_state)
            pcm = _even(pcm)
            if not pcm:
                return ""
        return base64.b64encode(pcm).decode()

    def heard_ms(self, nbytes: int) -> int:
        """Milliseconds of transport audio `nbytes` long, the clock the recorder already uses."""
        if self.pcmu:
            return nbytes * 1000 // PHONE_RATE
        return nbytes * 1000 // (self.pcm_rate * WIDTH)


def _even(pcm: bytes) -> bytes:
    """PCM16 is two bytes a sample. A torn tail is kept for the next chunk by dropping it here."""
    if len(pcm) % WIDTH:
        return pcm[:-1]
    return pcm
