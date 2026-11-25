from podatlas_pipeline.extract import format_transcript_for_llm, parse_llm_json
from podatlas_pipeline.models import TranscriptSegment
from podatlas_pipeline.podcast_index import parse_srt_or_vtt


def test_parse_llm_json_strips_markdown_fences():
    raw = """Here is the extracted data:
```json
{
  "isDestinationFocused": true,
  "primaryCities": [{"city": "Kyoto", "country": "Japan", "tagline": "Zen temples and matcha."}],
  "summary": "Explores Edo-era Kyoto.",
  "books": [{"title": "Kyoto: A Cultural History", "author": "John Dougill", "quote": "Great primer", "timestampSec": 840}],
  "placesAndGear": [{"name": "Nishiki Market", "type": "Food", "note": "Historic covered market", "timestampSec": 1950}]
}
```
"""
    parsed = parse_llm_json(raw)
    assert parsed.isDestinationFocused is True
    assert parsed.primaryCities[0].city == "Kyoto"
    assert parsed.books[0].timestampSec == 840
    assert parsed.placesAndGear[0].type == "Food"


def test_format_transcript_for_llm():
    segs = [
        TranscriptSegment(start=0.0, end=5.0, text="Welcome to Kyoto."),
        TranscriptSegment(start=65.4, end=70.0, text="Visit Nishiki Market."),
    ]
    out = format_transcript_for_llm(segs)
    assert "[00:00] Welcome to Kyoto." in out
    assert "[01:05] Visit Nishiki Market." in out


def test_parse_srt_or_vtt():
    vtt = """WEBVTT

00:00:10.000 --> 00:00:14.500
Hello from <b>Kyoto</b>!

00:01:02.000 --> 00:01:05.000
Check out Honen-in Temple.
"""
    segs = parse_srt_or_vtt(vtt)
    assert len(segs) == 2
    assert segs[0].start == 10.0
    assert segs[0].text == "Hello from Kyoto!"
    assert segs[1].start == 62.0
