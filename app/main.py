"""
main.py
FastAPI 메인 애플리케이션 — 날씨 + 일기 → Gemini 감정 분석 → 한국 음악 플레이리스트 반환
흐름: Gemini(곡 직접 추천) → iTunes Search API(커버/링크 보강)
"""

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.gemini import analyze_diary
from app.music import build_playlist

# ── 날씨 선택지 ───────────────────────────────────────────────────────────────

WEATHER_OPTIONS: list[dict] = [
    {"value": "맑음",  "label": "맑음 ☀️",   "emoji": "☀️"},
    {"value": "흐림",  "label": "흐림 ☁️",   "emoji": "☁️"},
    {"value": "비",    "label": "비 🌧️",     "emoji": "🌧️"},
    {"value": "눈",    "label": "눈 ❄️",     "emoji": "❄️"},
    {"value": "바람",  "label": "바람 💨",   "emoji": "💨"},
    {"value": "폭풍",  "label": "폭풍 ⛈️",   "emoji": "⛈️"},
    {"value": "안개",  "label": "안개 🌫️",   "emoji": "🌫️"},
    {"value": "더움",  "label": "더움 🌡️",   "emoji": "🌡️"},
    {"value": "추움",  "label": "추움 🧊",   "emoji": "🧊"},
]

VALID_WEATHER = {w["value"] for w in WEATHER_OPTIONS}

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    import os, logging
    from dotenv import load_dotenv
    load_dotenv()
    logger = logging.getLogger("uvicorn.error")
    if not os.getenv("GEMINI_API_KEY"):
        logger.warning("⚠️  환경변수 GEMINI_API_KEY 가 설정되지 않았습니다.")
    yield


# ── FastAPI 앱 ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="🎵 Mood Playlist API",
    description="날씨 + 일기 텍스트를 기반으로 Gemini AI가 감정을 분석하고 한국 음악 플레이리스트를 추천합니다.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 스키마 ─────────────────────────────────────────────────────────────────────

class RecommendRequest(BaseModel):
    weather: str = Field(..., description="날씨 선택지 (예: 비, 맑음)", examples=["비"])
    diary: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="오늘의 일기 또는 감정 메모",
        examples=["오늘은 비가 와서 우산을 들고 출근했다. 빗소리가 좋았지만 왠지 쓸쓸한 하루였다."],
    )
    track_count: int = Field(default=10, ge=3, le=20, description="추천 트랙 수 (3~20)")


class TrackItem(BaseModel):
    title: str
    artist: str
    lastfm_url: Optional[str] = None
    cover_url: Optional[str] = None
    spotify_url: Optional[str] = None
    preview_url: Optional[str] = None


class RecommendResponse(BaseModel):
    weather: str
    tags: list[str]
    tracks: list[TrackItem]


# ── 엔드포인트 ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    """서버 상태 확인"""
    return {"status": "ok", "version": "2.0.0"}


@app.get("/weather-options", tags=["System"])
async def get_weather_options():
    """날씨 선택지 목록 반환"""
    return {"options": WEATHER_OPTIONS}


@app.post("/recommend", response_model=RecommendResponse, tags=["Playlist"])
async def recommend_playlist(body: RecommendRequest):
    """
    날씨 + 일기 텍스트를 받아 Gemini가 한국 음악을 직접 추천합니다.

    **흐름**
    1. Gemini → 일기 감정 분석 → 무드 태그 3개 + 한국 노래 목록 추출
    2. iTunes Search API → 앨범 커버 + Apple Music 링크 보강
    3. 플레이리스트 카드 반환
    """
    if body.weather not in VALID_WEATHER:
        raise HTTPException(
            status_code=422,
            detail=f"지원하지 않는 날씨입니다. 선택 가능: {sorted(VALID_WEATHER)}",
        )

    # 1. Gemini: 감정 분석 + 한국 노래 추천
    try:
        result = await analyze_diary(body.weather, body.diary, count=body.track_count)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini 호출 실패: {e}")

    mood_tags = result["mood_tags"]
    songs = result["songs"]

    # 2. iTunes: 커버 + 링크 보강
    try:
        playlist = await build_playlist(songs, mood_tags)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"iTunes API 호출 실패: {e}")

    return RecommendResponse(
        weather=body.weather,
        tags=playlist["tags"],
        tracks=[TrackItem(**t) for t in playlist["tracks"]],
    )


# ── 정적 파일 (프론트엔드) ────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
