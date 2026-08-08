import json
import os
import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from dotenv import load_dotenv
load_dotenv()

model = joblib.load('Mental_Health_Model.pkl')
top_countries = ['Other','India','USA','Canada','Australia','UK','Germany','Mexico','Turkey','France']

# GenAI client — reads GROQ_API_KEY from environment (set this in Render's
# dashboard under Environment, never commit it to the repo)
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
GENAI_MODEL = "openai/gpt-oss-20b"  # fast + cheap on Groq; swap for openai/gpt-oss-120b for higher quality

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


#A first Pydantic Model
class StudentData(BaseModel):
    age                     : int = Field(..., ge=10, le=100)
    gender                  : Literal['Male', 'Female']
    country                 : str
    academic_level          : Literal['Undergraduate', 'Graduate', 'High School']
    most_used_platform      : Literal['Facebook', 'LinkedIn', 'Instagram', 'Snapchat','Twitter','YouTube', 'TikTok', 'LINE', 'KakaoTalk', 'VKontakte', 'WhatsApp','WeChat']
    purpose_of_use          : Literal['Networking', 'Education', 'Entertainment', 'News']
    avg_daily_usage_hours   : float = Field(..., ge=0, le=24)
    daily_unlocks           : int   = Field(..., ge=0)
    study_hours             : float = Field(..., ge=0, le=24)
    physical_activity_hours : float = Field(..., ge=0, le=24)
    sleep_hours_per_night   : float = Field(..., ge=0, le=24)
    stress_level            : Literal['Medium', 'Low', 'Very High', 'High']




# Describe what we send back
class PredictionResponse(BaseModel):
    predicted_mental_health_score:float
    #6.777777 -> float


# ---------------------------------------------------------------
# GenAI insights: same student profile + the score the model just
# produced, so the LLM explains the number instead of guessing it.
# ---------------------------------------------------------------
class InsightsRequest(StudentData):
    predicted_mental_health_score: float = Field(..., ge=0, le=10)


class Suggestion(BaseModel):
    category: Literal['sleep', 'screen_time', 'study', 'physical_activity', 'stress']
    title: str
    detail: str


class InsightsResponse(BaseModel):
    band: Literal['strained', 'balanced', 'strong']
    summary: str
    risk_factors: list[str] = []
    suggestions: list[Suggestion] = []
    encouragement: str = "Small, consistent changes tend to add up more than big overhauls — you're already paying attention, and that matters."


@app.get('/')
def greet():
    return {'this is ahmed'}


@app.post('/predict', response_model=PredictionResponse) #6.77777
def predict(data: StudentData):
   
   country_group = data.country if data.country in top_countries else "Other"

   input_row = pd.DataFrame([{
        'Age'                       :data.age,
        'Gender'                    :data.gender,
        'Country'                   :data.country,
        'Academic_Level'            :data.academic_level,
        'Most_Used_Platform'        :data.most_used_platform,
        'Purpose_Of_Use'            :data.purpose_of_use,
        'Avg_Daily_Usage_Hours'     :data.avg_daily_usage_hours,
        'Daily_Unlocks'             :data.daily_unlocks,
        'Study_Hours'               :data.study_hours,
        'Physical_Activity_Hours'   :data.physical_activity_hours,
        'Sleep_Hours_Per_Night'     :data.sleep_hours_per_night,
        'Stress_Level'              :data.stress_level,
        'Grouped_country'           :country_group
   }])

   prediction = model.predict(input_row)[0] #6.77
   return PredictionResponse(predicted_mental_health_score=round(float(prediction),2))


def _band_for(score: float) -> str:
    if score < 4:
        return "strained"
    if score < 7:
        return "balanced"
    return "strong"


SYSTEM_PROMPT = """You are a supportive student-wellness assistant embedded in a web app.
You are given a student's self-reported habits and a 0-10 "mental health score" that a
separate ML model already predicted from those habits. Your job is ONLY to explain and
contextualize that score and suggest small, concrete, achievable habit changes.

Rules:
- You are not a therapist and must never diagnose a condition or use clinical/diagnostic language.
- Be warm, specific, and non-alarmist. Reference the student's actual numbers (sleep hours,
  screen time, study hours, physical activity, stress level) when relevant.
- Suggestions must be small and realistic (e.g. "shift lights-out 30 minutes earlier"), not
  generic platitudes like "reduce stress".
- Output ONLY valid JSON matching this exact schema, no markdown fences, no extra text:
{
  "band": "strained" | "balanced" | "strong",
  "summary": "1-2 sentence reflection tied to their specific inputs and score",
  "risk_factors": ["short phrase", "short phrase", ...],   // 0-4 items, only real ones
  "suggestions": [
    {"category": "sleep"|"screen_time"|"study"|"physical_activity"|"stress",
     "title": "short actionable title",
     "detail": "1 sentence, concrete, tied to their numbers"}
  ],   // 2-4 items
  "encouragement": "one warm closing sentence, no medical claims"
}

Every key above is REQUIRED, including "encouragement" — never omit it, even under length pressure.
"""


def _build_user_prompt(data: "InsightsRequest") -> str:
    return json.dumps({
        "predicted_mental_health_score": data.predicted_mental_health_score,
        "band": _band_for(data.predicted_mental_health_score),
        "age": data.age,
        "academic_level": data.academic_level,
        "avg_daily_usage_hours": data.avg_daily_usage_hours,
        "daily_unlocks": data.daily_unlocks,
        "most_used_platform": data.most_used_platform,
        "purpose_of_use": data.purpose_of_use,
        "study_hours": data.study_hours,
        "physical_activity_hours": data.physical_activity_hours,
        "sleep_hours_per_night": data.sleep_hours_per_night,
        "stress_level": data.stress_level,
    })


@app.post('/insights', response_model=InsightsResponse)
def insights(data: InsightsRequest):
    if not os.environ.get("GROQ_API_KEY"):
        raise HTTPException(status_code=503, detail="GenAI insights are not configured on the server.")

    try:
        completion = groq_client.chat.completions.create(
            model=GENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(data)},
            ],
            temperature=0.6,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content
        parsed = json.loads(raw)

        # Be tolerant of an LLM that returns valid JSON but skips an
        # optional-ish key — fill sensible defaults instead of hard-failing
        # the whole request over one missing field.
        parsed.setdefault("band", _band_for(data.predicted_mental_health_score))
        parsed.setdefault("risk_factors", [])
        parsed.setdefault("suggestions", [])
        parsed.setdefault(
            "encouragement",
            "Small, consistent changes tend to add up more than big overhauls — you're already paying attention, and that matters.",
        )
        if not parsed.get("summary"):
            parsed["summary"] = "Here's a quick read on your current habits based on what you shared."

        return InsightsResponse(**parsed)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="GenAI returned a malformed response. Please retry.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GenAI insights failed: {exc}")