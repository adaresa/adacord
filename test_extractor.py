from source_utils import format_duration, is_url, spotify_playlist_id


def test_url_detection() -> None:
    assert is_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_url("http://example.com/audio")
    assert not is_url("daft punk one more time")


def test_spotify_playlist_id() -> None:
    assert (
        spotify_playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        == "37i9dQZF1DXcBWIGoYBM5M"
    )
    assert (
        spotify_playlist_id("https://open.spotify.com/intl-ee/playlist/abc123?si=token")
        == "abc123"
    )
    assert spotify_playlist_id("https://open.spotify.com/track/abc123") is None


def test_duration_formatting() -> None:
    assert format_duration(None) == ""
    assert format_duration(0) == ""
    assert format_duration(65_000) == "1:05"
    assert format_duration(3_665_000) == "1:01:05"


if __name__ == "__main__":
    test_url_detection()
    test_spotify_playlist_id()
    test_duration_formatting()
    print("OK")

