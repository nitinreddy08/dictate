"""Transcribing while you talk, so the text is ready the moment you stop."""
import threading

from . import RATE


def speech_in(audio, **vad):
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return get_speech_timestamps(audio, vad_options=VadOptions(**vad)) if vad else get_speech_timestamps(audio)


class Transcription:
    """Transcribes a recording that is still growing. Whenever you pause, everything said so far is
    transcribed in the background as one piece, so if you stop right after a pause the text is already
    done. `get_audio()` returns all audio recorded so far (float32, 16 kHz mono)."""

    PAUSE = 0.4       # seconds of silence after which we transcribe what's been said so far
    MAX_PIECE = 20.0  # seconds; longer recordings are split at a pause (Whisper hears 30 s at a time)

    def __init__(self, model, get_audio):
        self.model, self.get_audio = model, get_audio
        self.texts, self.done = [], 0  # finished pieces of a long recording; samples they cover
        self.guess = (None, None, "")  # (done, speech end, text) for everything after `done`
        self.stop_flag = threading.Event()
        self.worker = threading.Thread(target=self._pretranscribe, daemon=True)
        self.worker.start()

    def _pretranscribe(self):
        while not self.stop_flag.wait(0.5):
            if not self.model.ready.is_set():
                continue
            rest = self.get_audio()[self.done:]
            if len(rest) <= RATE // 2:
                continue
            speech = speech_in(rest, min_silence_duration_ms=int(self.PAUSE * 1000))
            ends = [s["end"] for s in speech if s["end"] + self.PAUSE * RATE <= len(rest)]
            if not ends:
                continue
            context = " ".join(self.texts)
            if ends[-1] > self.MAX_PIECE * RATE:  # long recording: finish this piece at the pause
                cut = ends[-1] + int(0.2 * RATE)
                self.texts.append(self.model.transcribe(rest[:cut], context))
                self.done += cut
            elif ends[-1] == speech[-1]["end"] and self.guess[:2] != (self.done, ends[-1]):
                # Paused after the latest speech: transcribe it all in case you stop here
                text = self.model.transcribe(rest[:ends[-1] + int(0.2 * RATE)], context)
                self.guess = (self.done, ends[-1], text)

    def finish(self, audio):
        """Called once recording has stopped, with the complete audio. Returns the raw text."""
        self.stop_flag.set()
        self.worker.join()
        rest = audio[self.done:]
        if not self._speech_after(rest, -int(0.2 * RATE)):
            return " ".join(t for t in self.texts if t).strip()  # no speech: never ask Whisper
        done, end, text = self.guess
        if done == self.done and not self._speech_after(rest, end):
            self.texts.append(text)  # nothing new since the pause: already transcribed
        elif len(rest) > 0.3 * RATE:
            self.texts.append(self.model.transcribe(rest, " ".join(self.texts)))
        return " ".join(t for t in self.texts if t).strip()

    @staticmethod
    def _speech_after(rest, end):
        after = rest[end + int(0.2 * RATE):]
        return len(after) > 0.3 * RATE and bool(speech_in(after))
