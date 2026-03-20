"""
gemini.py
Gemini가 날씨 + 일기 내용을 분석해 무드 태그와 한국 노래 목록을 직접 추천합니다.
"""

import os
import json
import re
from google import genai
from google.genai import types

GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """
You are a Korean music curator. Read the diary, infer emotion coordinates, then recommend K-Pop songs.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "diary": "<한국어 일기>",
  "weather": "<날씨: 맑음|흐림|비|눈|바람|폭풍|안개|더움|추움>",
  "count": <1–10>
}}

- count 범위 초과 시: 1 미만→1, 10 초과→10으로 처리

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 0 — SENTIMENT INFERENCE (V-A 추정)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
일기 전체를 읽고 두 축을 -1.0~1.0 범위로 추정하세요.
  valence : 감정의 긍정(+) / 부정(-) 정도
  arousal : 활성화·흥분(+) / 차분·무기력(-) 정도
추정한 값을 이후 LEVEL 2 로직의 ground truth로 사용합니다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CURATION LOGIC (우선순위 순)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LEVEL 1 — LYRICAL SYNC
  일기의 구체적 행동(e.g. 야근, 지하철)이 실존 K-Pop 곡의 핵심 테마와
  90% 이상 확신될 때만 적용. 아니면 Level 2.

LEVEL 2 — V-A 기반 에너지 티어
  valence >  0.3 & arousal >  0.3 → ENERGETIC  (BPM 120+)
  valence < -0.3 & arousal >  0.3 → INTENSE    (강렬, 카타르틱)
  valence >  0.3 & arousal < -0.3 → CALM       (따뜻, 어쿠스틱)
  valence < -0.3 & arousal < -0.3 → MELANCHOLIC (발라드, 로파이)
  그 외                            → AMBIGUOUS → Level 3

  마지막 문장 감정 보정:
    "positive" → 한 티어 위로 / "negative" → 한 티어 아래로 / "mixed" → 유지

LEVEL 3 — 날씨 fallback (AMBIGUOUS일 때만)
  비·눈·폭풍·안개·흐림 → 저BPM, 무드, 어쿠스틱
  맑음·더움             → 중고BPM, 밝고 청량
  그 외                 → CALM 티어 적용

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Apple Music Korea 실존 곡만 추천. 불확실하면 skip.
- 가사 직접 인용 금지. 분위기·테마로만 연결.
- 아티스트 중복 금지. count >= 4: 연도(~2015 / 2020~), 성별, 솔로/그룹 혼합.
- reason은 추정한 V-A 수치 + 일기 속 행동을 모두 언급할 것.
- 출력은 JSON만. 마크다운 금지. reason·curation_strategy는 한국어(해요체).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "extracted": {{
    "inferred_valence": <float -1.0~1.0>,
    "inferred_arousal": <float -1.0~1.0>,
    "key_actions": ["action1", "action2"],
    "lyric_sync_candidates": ["candidate1"],
    "ending_sentiment": "positive|negative|mixed"
  }},
  "mood_tags": ["#태그1", "#태그2", "#태그3"],
  "curation_strategy": "적용 레벨 및 V(X.X) A(X.X) 기반 근거 (2문장 이내)",
  "songs": [{{
    "title": "곡 제목",
    "artist": "아티스트명",
    "reason": "V-A 수치·일기 행동 연결 근거",
    "search_query": "Artist SongTitle"
  }}]
}}
"""


async def analyze_diary(weather: str, diary: str, count: int = 15) -> dict:
    """
    날씨 + 일기를 분석해 무드 태그와 한국 노래 목록을 반환합니다.

    Returns:
        {
          "mood_tags": ["tag1", "tag2", "tag3"],
          "songs": [{"title": str, "artist": str}, ...]
        }
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")

    client = genai.Client(api_key=api_key)
    user_message = json.dumps(
        {"diary": diary, "weather": weather, "count": count},
        ensure_ascii=False,
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.8,
            max_output_tokens=4096,
        ),
    )

    raw = response.text or ""
    return _parse_response(raw)


def _parse_response(raw: str) -> dict:
    """Gemini 응답에서 JSON을 파싱합니다."""
    raw = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Gemini 응답에서 JSON을 찾을 수 없습니다: {raw[:200]}")

    data = json.loads(match.group())

    mood_tags = [str(t).strip() for t in data.get("mood_tags", [])[:3]]
    songs = [
        {"title": str(s.get("title", "")).strip(), "artist": str(s.get("artist", "")).strip()}
        for s in data.get("songs", [])
        if s.get("title") and s.get("artist")
    ]

    while len(mood_tags) < 3:
        mood_tags.append(["감성", "잔잔함", "한국음악"][len(mood_tags)])

    return {"mood_tags": mood_tags, "songs": songs}
