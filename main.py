from frontend_routes import install_frontend
from pydantic import BaseModel, Field
from llm_client import LLMError
from sql_executor import execute_sql
from sql_generator import generate_sql
from analysis_service import analyze_question
from audio_routes import router as audio_router
from fastapi import FastAPI, HTTPException, UploadFile
from dataset_store import (
    MAX_UPLOAD_BYTES,
    get_dataset_info,
    save_csv_dataset,
)

app = FastAPI(
    title="Voice-Based Data Analysis API",
    version="0.1.0",
)

app.include_router(audio_router)
install_frontend(app)

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/datasets", status_code=201)
def upload_dataset(file: UploadFile):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Upload a .csv file.",
        )

    content = file.file.read(MAX_UPLOAD_BYTES + 1)

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="The file exceeds 10 MB.",
        )

    try:
        return save_csv_dataset(content)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

@app.get("/datasets/{dataset_id}")
def read_dataset(dataset_id: str):
    try:
        return get_dataset_info(dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

class SQLRequest(BaseModel):
    dataset_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    question: str = Field(min_length=1, max_length=2000)


@app.post("/generate-sql")
def generate_sql_endpoint(request: SQLRequest):
    try:
        return generate_sql(
            request.dataset_id,
            request.question,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except LLMError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

class ExecuteSQLRequest(BaseModel):
    dataset_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sql: str = Field(min_length=1, max_length=20_000)


@app.post("/execute-sql")
def execute_sql_endpoint(request: ExecuteSQLRequest):
    try:
        return execute_sql(request.dataset_id, request.sql)

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except TimeoutError as exc:
        raise HTTPException(
            status_code=408,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

@app.post("/analyze/text")
def analyze_text_endpoint(request: SQLRequest):
    try:
        return analyze_question(
            request.dataset_id,
            request.question,
        )

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except TimeoutError as exc:
        raise HTTPException(
            status_code=408,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except LLMError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc