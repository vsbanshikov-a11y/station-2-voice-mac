"""Recognize an explicit mode phrase locally; disabled until wired to a caller."""
import hashlib
import os
import shutil
from pathlib import Path
import subprocess
import tempfile

from mode_switch import intent

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / 'models/ggml-base-q5_1.bin'
MODEL_SHA256 = '422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898'
WHISPER = os.environ.get('STATION_WHISPER_CLI') or shutil.which('whisper-cli') or '/opt/homebrew/bin/whisper-cli'


def verify_model(path=MODEL):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest() == MODEL_SHA256


def recognize(wav_data, *, model=MODEL):
    """Return only the mode intent; never print or persist the transcript."""
    if not isinstance(wav_data, bytes) or not 44 <= len(wav_data) <= 320_000:
        raise ValueError('invalid audio size')
    if not verify_model(model):
        raise RuntimeError('speech model failed integrity check')
    # The unnamed, owner-only file is removed automatically and passed to the
    # child via an inherited file descriptor. No named recording is retained.
    with tempfile.TemporaryFile(mode='w+b', dir='/private/tmp') as audio:
        os.fchmod(audio.fileno(), 0o600)
        audio.write(wav_data)
        audio.flush()
        audio.seek(0)
        fd = audio.fileno()
        result = subprocess.run([WHISPER, '-m', str(model), '-l', 'ru', '-t', '4',
                                 '-nt', '-np', '-ng', '-f', f'/dev/fd/{fd}'],
                                pass_fds=(fd,), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError('local speech recognition failed')
    return intent(result.stdout.decode('utf-8', errors='replace'))
