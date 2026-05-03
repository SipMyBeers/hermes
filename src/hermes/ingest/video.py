"""Video URL extraction (Instagram reels, YouTube, TikTok, X).

v1.3 implementation:
- subprocess.run(['yt-dlp', '-x', '--audio-format', 'wav', url])
- pipe audio through whisper-cpp (see hermes.voice.transcribe)
- return ExtractedDoc(title=metadata, text=transcript, url=url)

Both yt-dlp and whisper-cpp must be installed on the host (brew install yt-dlp;
brew install whisper-cpp + download a ggml model into ~/.hermes/whisper/).
"""
from __future__ import annotations
from hermes.ingest.web import ExtractedDoc


def extract_video(url: str) -> ExtractedDoc:
    raise NotImplementedError(
        "v1.3: yt-dlp audio extraction + whisper-cpp transcription "
        "(see module docstring for install steps)"
    )
