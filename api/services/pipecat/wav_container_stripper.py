"""Keep only the audio of a WAV response from a speech endpoint.

Pipecat's OpenAI-compatible TTS client requests raw PCM and treats the response
body as samples. Some routers ignore that request and answer with a complete
WAV file, so the RIFF header is played as a click and any chunk after the audio
data — a C2PA provenance manifest, for instance — is played as a burst of noise
once the sentence ends.

This processor leaves a raw PCM stream untouched and, for a WAV stream, emits
only the bytes declared by the ``data`` chunk as they arrive.

A turn's sentences share one TTS context, so several complete WAV responses can
arrive with a single ``TTSStartedFrame``. Each response's header also restarts
detection; otherwise the second response of a turn would be taken for container
metadata and dropped.
"""

from __future__ import annotations

import struct
from dataclasses import replace

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

_RIFF_HEADER_BYTES = 12
# A real container header is a few dozen bytes. Bail out to pass-through if the
# endpoint sends something that neither streams audio nor resolves to a chunk.
_MAX_HEADER_BYTES = 64 * 1024


def _is_wav_header(audio: bytes) -> bool:
    return (
        len(audio) >= _RIFF_HEADER_BYTES
        and audio[:4] == b"RIFF"
        and audio[8:12] == b"WAVE"
    )


class WavContainerStripper(FrameProcessor):
    """Emit the PCM payload of a WAV stream and drop the container bytes.

    Detection restarts on every ``TTSStartedFrame`` and on every fresh RIFF/WAVE
    header, and a stream that does not begin with one is passed through
    unchanged.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._buffer = bytearray()
        self._held_frame: TTSAudioRawFrame | None = None
        self._mode = "detect"
        self._remaining: int | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStartedFrame):
            self._reset()
            await self.push_frame(frame, direction)
        elif isinstance(frame, TTSAudioRawFrame):
            if self._mode != "detect" and _is_wav_header(frame.audio):
                # A new response in the same turn: request N+1 starts here, so
                # request N's consumed-data state no longer applies.
                self._reset()
            await self._process_audio(frame, direction)
        elif isinstance(frame, TTSStoppedFrame):
            await self._flush_short_stream(direction)
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    def _reset(self) -> None:
        self._buffer.clear()
        self._held_frame = None
        self._mode = "detect"
        self._remaining = None

    async def _flush_short_stream(self, direction: FrameDirection) -> None:
        """Release bytes held for header detection on a stream that ended early."""
        if self._mode == "detect" and self._buffer and self._held_frame is not None:
            audio = bytes(self._buffer)
            self._buffer.clear()
            await self.push_frame(replace(self._held_frame, audio=audio), direction)
        self._reset()

    async def _process_audio(
        self, frame: TTSAudioRawFrame, direction: FrameDirection
    ) -> None:
        if self._mode == "drop":
            return
        if self._mode == "pass":
            await self.push_frame(frame, direction)
            return
        if self._mode == "audio":
            await self._push_samples(frame, frame.audio, direction)
            return

        self._buffer.extend(frame.audio)
        self._held_frame = frame
        if len(self._buffer) < _RIFF_HEADER_BYTES:
            return
        if self._buffer[:4] != b"RIFF" or self._buffer[8:12] != b"WAVE":
            await self._start_passthrough(frame, direction)
            return

        pos = _RIFF_HEADER_BYTES
        while True:
            if pos + 8 > len(self._buffer):
                if len(self._buffer) > _MAX_HEADER_BYTES:
                    logger.warning(
                        "Gave up parsing a WAV header after {} bytes; passing it through",
                        len(self._buffer),
                    )
                    await self._start_passthrough(frame, direction)
                return
            chunk_id = bytes(self._buffer[pos : pos + 4])
            chunk_size = struct.unpack("<I", self._buffer[pos + 4 : pos + 8])[0]
            if chunk_id == b"data":
                payload = bytes(self._buffer[pos + 8 :])
                self._buffer.clear()
                self._held_frame = None
                self._mode = "audio"
                self._remaining = chunk_size if 0 < chunk_size < 0xFFFFFFFF else None
                await self._push_samples(frame, payload, direction)
                return
            # Chunks are word aligned: an odd-sized chunk carries a pad byte.
            pos += 8 + chunk_size + (chunk_size & 1)

    async def _start_passthrough(
        self, frame: TTSAudioRawFrame, direction: FrameDirection
    ) -> None:
        audio = bytes(self._buffer)
        self._buffer.clear()
        self._held_frame = None
        self._mode = "pass"
        await self.push_frame(replace(frame, audio=audio), direction)

    async def _push_samples(
        self, frame: TTSAudioRawFrame, audio: bytes, direction: FrameDirection
    ) -> None:
        if self._remaining is not None:
            audio = audio[: self._remaining]
            self._remaining -= len(audio)
            if self._remaining == 0:
                # Everything past the declared audio is container metadata.
                self._mode = "drop"
        if audio:
            await self.push_frame(replace(frame, audio=audio), direction)
