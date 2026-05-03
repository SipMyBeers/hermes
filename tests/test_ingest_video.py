import pytest
from hermes.ingest.video import extract_video


def test_extract_video_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="v1.3"):
        extract_video("https://www.instagram.com/reel/abc/")
