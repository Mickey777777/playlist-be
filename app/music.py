"""
music.py
Gemini가 추천한 한국 노래 목록을 iTunes Search API로 커버/링크 보강합니다.
Last.fm 의존성 완전 제거.
"""

import logging
import asyncio
import httpx

logger = logging.getLogger(__name__)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


async def _enrich_with_itunes(
    client: httpx.AsyncClient,
    title: str,
    artist: str,
) -> dict:
    """iTunes Search API로 앨범 커버 + Apple Music 링크 조회 (3단계 폴백)"""

    queries = [
        f"{title} {artist}",  # 1단계: 제목 + 아티스트
        title,                # 2단계: 제목만
        artist,               # 3단계: 아티스트만 (커버라도 가져오기)
    ]

    for term in queries:
        try:
            resp = await client.get(
                ITUNES_SEARCH_URL,
                params={"term": term, "media": "music", "limit": 1, "country": "KR"},
                timeout=10.0,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                r = results[0]
                cover = r.get("artworkUrl100", "").replace("100x100bb", "500x500bb")
                return {
                    "cover_url":   cover or None,
                    "spotify_url": r.get("trackViewUrl"),
                    "preview_url": r.get("previewUrl"),
                }
        except Exception as e:
            logger.debug("iTunes 검색 실패 term=%r: %s", term, e)
            continue

    logger.warning("iTunes 링크 없음: %s - %s", title, artist)
    return {"cover_url": None, "spotify_url": None, "preview_url": None}


async def build_playlist(songs: list[dict], mood_tags: list[str]) -> dict:
    """
    Gemini가 추천한 곡 목록을 iTunes로 보강해 플레이리스트를 완성합니다.

    Args:
        songs:     [{"title": str, "artist": str}, ...]
        mood_tags: Gemini가 분석한 무드 태그 3개

    Returns:
        {
          "tags": [...],
          "tracks": [
            {
              "title", "artist",
              "cover_url", "spotify_url", "preview_url"
            }, ...
          ]
        }
    """
    async with httpx.AsyncClient() as client:
        tasks = [
            _enrich_with_itunes(client, s["title"], s["artist"])
            for s in songs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    tracks = []
    for i, song in enumerate(songs):
        meta = (
            results[i]
            if not isinstance(results[i], Exception)
            else {"cover_url": None, "spotify_url": None, "preview_url": None}
        )
        tracks.append({
            "title":       song["title"],
            "artist":      song["artist"],
            "lastfm_url":  None,
            **meta,
        })

    return {"tags": mood_tags, "tracks": tracks}
