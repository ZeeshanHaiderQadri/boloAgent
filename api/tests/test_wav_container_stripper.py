"""Unit tests for the WAV container stripper used by the voice pipeline."""

import struct

from pipecat.frames.frames import (
    Frame,
    TextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.wav_container_stripper import WavContainerStripper

SAMPLE_RATE = 24000


def _wav(
    payload: bytes,
    *,
    before_data: bytes = b"",
    after_data: bytes = b"",
    declared_size: int | None = None,
) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + before_data
    size = len(payload) if declared_size is None else declared_size
    body = chunks + b"data" + struct.pack("<I", size) + payload + after_data
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


class _Capture(WavContainerStripper):
    def __init__(self):
        super().__init__()
        self.frames: list[Frame] = []
        self.audio: list[bytes] = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.frames.append(frame)
        if isinstance(frame, TTSAudioRawFrame):
            self.audio.append(frame.audio)


async def _run(chunks: list[bytes], *, capture: _Capture | None = None) -> _Capture:
    capture = capture or _Capture()
    await capture.process_frame(TTSStartedFrame(), FrameDirection.DOWNSTREAM)
    for chunk in chunks:
        await capture.process_frame(
            TTSAudioRawFrame(audio=chunk, sample_rate=SAMPLE_RATE, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
    await capture.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
    return capture


async def test_raw_pcm_passes_through_unchanged():
    pcm = bytes(range(256)) * 4

    capture = await _run([pcm[:100], pcm[100:]])

    assert b"".join(capture.audio) == pcm


async def test_wav_header_and_trailing_metadata_are_dropped():
    pcm = b"\x10\x00" * 500
    trailer = b"C2PA" + struct.pack("<I", 64) + b"\xff" * 64

    capture = await _run([_wav(pcm, after_data=trailer)])

    assert b"".join(capture.audio) == pcm


async def test_container_is_parsed_across_small_chunks():
    pcm = bytes(range(200)) * 3
    wav = _wav(pcm, after_data=b"C2PA" + struct.pack("<I", 8) + b"\x00" * 8)
    chunks = [wav[index : index + 7] for index in range(0, len(wav), 7)]

    capture = await _run(chunks)

    assert b"".join(capture.audio) == pcm


async def test_unknown_chunk_before_data_is_skipped():
    pcm = b"\x01\x02" * 64
    # An odd-sized LIST chunk carries a pad byte before the next chunk header.
    before_data = b"LIST" + struct.pack("<I", 3) + b"abc\x00"

    capture = await _run([_wav(pcm, before_data=before_data)])

    assert b"".join(capture.audio) == pcm


async def test_declared_data_size_caps_the_audio():
    pcm = b"\x05\x06" * 48
    extra = b"\x99" * 80

    capture = await _run([_wav(pcm + extra, declared_size=len(pcm))])

    assert b"".join(capture.audio) == pcm


async def test_stream_shorter_than_a_header_is_released_on_stop():
    pcm = b"\x01\x02\x03"

    capture = await _run([pcm])

    assert b"".join(capture.audio) == pcm


async def test_each_request_is_inspected_separately():
    first = b"\x11" * 40
    second = b"\x22" * 40

    capture = await _run([_wav(first)])
    await capture.process_frame(TTSStartedFrame(), FrameDirection.DOWNSTREAM)
    await capture.process_frame(
        TTSAudioRawFrame(audio=second, sample_rate=SAMPLE_RATE, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    await capture.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)

    assert b"".join(capture.audio) == first + second


async def test_second_response_in_the_same_turn_is_also_stripped():
    first = b"\x11\x22" * 300
    second = b"\x33\x44" * 500
    trailer = b"C2PA" + struct.pack("<I", 16) + b"\xee" * 16

    capture = _Capture()
    await capture.process_frame(TTSStartedFrame(), FrameDirection.DOWNSTREAM)
    # One TTS context covers every sentence of a turn, so the second request
    # arrives with no TTSStartedFrame between them.
    for wav in (
        _wav(first, after_data=trailer),
        _wav(second, after_data=trailer),
    ):
        for index in range(0, len(wav), 4096):
            await capture.process_frame(
                TTSAudioRawFrame(
                    audio=wav[index : index + 4096],
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                ),
                FrameDirection.DOWNSTREAM,
            )
    await capture.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)

    assert b"".join(capture.audio) == first + second


async def test_wav_after_a_pcm_response_is_also_stripped():
    pcm = b"\x07\x08" * 100
    second = b"\x09\x0a" * 200

    capture = _Capture()
    await capture.process_frame(TTSStartedFrame(), FrameDirection.DOWNSTREAM)
    await capture.process_frame(
        TTSAudioRawFrame(audio=pcm, sample_rate=SAMPLE_RATE, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    await capture.process_frame(
        TTSAudioRawFrame(audio=_wav(second), sample_rate=SAMPLE_RATE, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    await capture.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)

    assert b"".join(capture.audio) == pcm + second


async def test_other_frames_are_forwarded():
    capture = await _run([])
    await capture.process_frame(TextFrame(text="hello"), FrameDirection.DOWNSTREAM)

    assert any(isinstance(frame, TextFrame) for frame in capture.frames)
    assert any(isinstance(frame, TTSStartedFrame) for frame in capture.frames)
    assert any(isinstance(frame, TTSStoppedFrame) for frame in capture.frames)
