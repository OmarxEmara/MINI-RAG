import time
from fastapi import FastAPI, APIRouter, status, Request
from fastapi.responses import JSONResponse
from routes.schemes.nlp import PushRequest, SearchRequest
from models.ProjectModel import ProjectModel
from models.ChunkModel import ChunkModel
from controllers import NLPController
from models import ResponseSignal
from tqdm.auto import tqdm
import time
import logging
import os
import json
import uuid
from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger('uvicorn.error')

# -----------------------------------------------------------------------------
# Media / TTS utilities and helpers
# -----------------------------------------------------------------------------
from fastapi.responses import FileResponse
import asyncio, io, tempfile
from functools import partial
from gtts import gTTS

# Response header safety + temp-file cleanup
import os
import base64
from starlette.background import BackgroundTask

# In-memory answer cache + small helpers
from typing import Optional, Dict, Any
from uuid import uuid4
import re

nlp_router = APIRouter(
    prefix="/api/v1/nlp",
    tags=["api_v1", "nlp"],
)

#############################################

class FeedbackRequest(BaseModel):
    answer_id: str
    feedback: int  # 0 or 1

# =============================================================================
# Answer cache (answer_id -> {project_id, answer, ts})
#  - Enables “ask once, play later” flow for audio without re-querying RAG.
#  - Short-lived to avoid memory growth.
# =============================================================================
TTL_SECONDS = 600  # keep answers for 10 minutes

def _get_cache(app) -> Dict[str, Any]:
    """Return the process-local cache stored on app.state."""
    if not hasattr(app.state, "answer_cache"):
        app.state.answer_cache = {}
    return app.state.answer_cache

def _purge_expired(app) -> None:
    """Drop expired cache entries based on TTL."""
    cache = _get_cache(app)
    now = time.time()
    for k in list(cache.keys()):
        if now - cache[k]["ts"] > TTL_SECONDS:
            cache.pop(k, None)

def cache_put_answer(app, project_id: int, answer: str) -> str:
    """Store an answer and return its generated answer_id."""
    _purge_expired(app)
    answer_id = str(uuid4())
    _get_cache(app)[answer_id] = {"project_id": project_id, "answer": answer, "ts": time.time()}
    return answer_id

def cache_get_answer(app, answer_id: str) -> Optional[str]:
    """Fetch a previously stored answer by ID, or None if missing/expired."""
    _purge_expired(app)
    item = _get_cache(app).get(answer_id)
    if not item:
        return None
    return item["answer"]

# =============================================================================
# Language detection and TTS
#  - Simple heuristic: Arabic characters -> "ar", else "en".
#  - gTTS synthesis returns MP3 bytes in-memory.
# =============================================================================
def _detect_lang(text: str) -> str:
    """Very simple language heuristic: Arabic block -> 'ar', else 'en'."""
    if re.search(r'[\u0600-\u06FF]', text):
        return "ar"
    return "en"

def _gtts_mp3_bytes(text: str, lang: Optional[str] = None) -> bytes:
    """Synthesize text to MP3 using gTTS; auto-select Arabic/English when not specified."""
    if not lang:
        lang = _detect_lang(text)
    buf = io.BytesIO()
    gTTS(text=text, lang=lang, slow=False, tld="com").write_to_fp(buf)
    return buf.getvalue()

# ===== Utility to save feedback locally =====
def save_feedback(project_id: str, answer_id: str, feedback: int):
    filename = f"feedback_{project_id}.json"
    data = []

    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = []

    data.append({"answer_id": answer_id, "feedback": feedback})

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# =============================================================================
# Indexing endpoints
# =============================================================================
@nlp_router.post("/index/push/{project_id}")
async def index_project(request: Request, project_id: int, push_request: PushRequest):
    """
    Batch-index a project's chunks into the vector DB.
    - Creates the collection (optionally reset).
    - Streams through chunks with pagination.
    - Returns total inserted count.
    """
    start_time = time.time()
    project_model = await ProjectModel.create_instance(
        db_client=request.app.db_client
    )

    chunk_model = await ChunkModel.create_instance(
        db_client=request.app.db_client
    )

    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )

    if not project:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"signal": ResponseSignal.PROJECT_NOT_FOUND_ERROR.value}
        )
    end_time   = time.time()
    sss = end_time - start_time  # kept from original code (timing scratch)

    nlp_controller = NLPController(
        vectordb_client=request.app.vectordb_client,
        generation_client=request.app.generation_client,
        embedding_client=request.app.embedding_client,
        template_parser=request.app.template_parser,
    )

    has_records = True
    page_no = 1
    inserted_items_count = 0
    idx = 0

    # Create collection if not exists (and reset if requested)
    collection_name = nlp_controller.create_collection_name(project_id=project.project_id)
    _ = await request.app.vectordb_client.create_collection(
        collection_name=collection_name,
        embedding_size=request.app.embedding_client.embedding_size,
        do_reset=push_request.do_reset,
    )

    # Batch through chunk pages
    total_chunks_count = await chunk_model.get_total_chunks_count(project_id=project.project_id)
    pbar = tqdm(total=total_chunks_count, desc="Vector Indexing", position=0)

    while has_records:
        page_chunks = await chunk_model.get_poject_chunks(project_id=project.project_id, page_no=page_no)
        if len(page_chunks):
            page_no += 1
        
        if not page_chunks or len(page_chunks) == 0:
            has_records = False
            break

        chunks_ids = [c.chunk_id for c in page_chunks]
        idx += len(page_chunks)
        
        is_inserted = await nlp_controller.index_into_vector_db(
            project=project,
            chunks=page_chunks,
            chunks_ids=chunks_ids
        )
        if not is_inserted:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"signal": ResponseSignal.INSERT_INTO_VECTORDB_ERROR.value}
            )

        pbar.update(len(page_chunks))
        inserted_items_count += len(page_chunks)
        
    return JSONResponse(
        content={
            "signal": ResponseSignal.INSERT_INTO_VECTORDB_SUCCESS.value,
            "inserted_items_count": inserted_items_count
        }
    )

@nlp_router.get("/index/info/{project_id}")
async def get_project_index_info(request: Request, project_id: int):
    """Return low-level vector DB collection metadata for a project."""
    project_model = await ProjectModel.create_instance(
        db_client=request.app.db_client
    )

    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )

    nlp_controller = NLPController(
        vectordb_client=request.app.vectordb_client,
        generation_client=request.app.generation_client,
        embedding_client=request.app.embedding_client,
        template_parser=request.app.template_parser,
    )

    collection_info = await nlp_controller.get_vector_db_collection_info(project=project)

    return JSONResponse(
        content={
            "signal": ResponseSignal.VECTORDB_COLLECTION_RETRIEVED.value,
            "collection_info": collection_info
        }
    )

@nlp_router.post("/index/search/{project_id}")
async def search_index(request: Request, project_id: int, search_request: SearchRequest):
    """Vector similarity search against a project's collection."""
    project_model = await ProjectModel.create_instance(
        db_client=request.app.db_client
    )

    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )

    nlp_controller = NLPController(
        vectordb_client=request.app.vectordb_client,
        generation_client=request.app.generation_client,
        embedding_client=request.app.embedding_client,
        template_parser=request.app.template_parser,
    )

    results = await nlp_controller.search_vector_db_collection(
        project=project, text=search_request.text, limit=search_request.limit
    )

    if not results:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"signal": ResponseSignal.VECTORDB_SEARCH_ERROR.value}
        )
    
    return JSONResponse(
        content={
            "signal": ResponseSignal.VECTORDB_SEARCH_SUCCESS.value,
            "results": [result.dict() for result in results]
        }
    )

# =============================================================================
# RAG answer + one-question audio flow
#  - POST returns text + response_time + answer_id + audio_url
#  - GET uses answer_id to speak the exact same answer (no re-asking)
# =============================================================================
@nlp_router.post("/index/answer/{project_id}")
async def answer_rag(
    request: Request,
    project_id: int,
    search_request: SearchRequest,
):
    """
    Generate a RAG answer for the given query and return:
      - answer (text), full_prompt, chat_history
      - response_time (seconds)
      - answer_id + audio_url to play the same answer later
    """
    start_time = time.time()

    project_model = await ProjectModel.create_instance(
        db_client=request.app.db_client
    )

    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )

    nlp_controller = NLPController(
        vectordb_client=request.app.vectordb_client,
        generation_client=request.app.generation_client,
        embedding_client=request.app.embedding_client,
        template_parser=request.app.template_parser,
    )

    answer, full_prompt, chat_history = await nlp_controller.answer_rag_question(
        project=project,
        query=search_request.text,
        limit=search_request.limit,
    )

    response_time = round(time.time() - start_time, 4)

    if not answer:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"signal": ResponseSignal.RAG_ANSWER_ERROR.value}
        )

    # Store the exact answer so the audio endpoint can speak it later.
    answer_id = cache_put_answer(request.app, project_id, answer)
    audio_url = f"/api/v1/nlp/index/answer/audio/{project_id}?answer_id={answer_id}"

    return JSONResponse(
        content={
            "signal": ResponseSignal.RAG_ANSWER_SUCCESS.value,
            "answer": answer,
            "full_prompt": full_prompt,
            "chat_history": chat_history,
            "response_time": response_time,
            "answer_id": answer_id,
            "audio_url": audio_url,
        }
    )

@nlp_router.get("/index/answer/audio/{project_id}")
async def answer_rag_audio_get(
    request: Request,
    project_id: int,
    answer_id: str,
):
    """
    Speak a previously generated answer by its answer_id.
    - Reads from in-memory cache; does not re-run RAG.
    - Returns a seekable MP3 (works in Swagger/browser players).
    """
    start_time = time.time()

    # Swagger sometimes includes quotes around string params
    answer_id = answer_id.strip().strip('"').strip("'")

    project_model = await ProjectModel.create_instance(db_client=request.app.db_client)
    _ = await project_model.get_project_or_create_one(project_id=project_id)

    # Retrieve the exact same answer text we returned in POST
    answer = cache_get_answer(request.app, answer_id)
    if answer is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"signal": "ANSWER_ID_NOT_FOUND_OR_EXPIRED"}
        )

    # gTTS is blocking; run in a thread so we don't block the event loop
    loop = asyncio.get_running_loop()
    mp3_bytes = await loop.run_in_executor(None, partial(_gtts_mp3_bytes, answer))

    # Write to a temp file so browsers/Swagger can perform HTTP Range requests
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp.write(mp3_bytes)
    tmp.flush(); tmp.close()

    # Use ASCII-safe headers (preview is base64) to avoid Unicode header errors
    preview_b64 = base64.b64encode(answer[:120].encode("utf-8")).decode("ascii")
    headers = {
        "X-Response-Time": str(round(time.time() - start_time, 4)),
        "X-Answer-Preview-Base64": preview_b64,
        "Accept-Ranges": "bytes",
        "Content-Disposition": 'inline; filename="answer.mp3"',
    }

    # Delete the temp file after the response is sent
    cleanup = BackgroundTask(lambda: os.remove(tmp.name))

    return FileResponse(
        tmp.name,
        media_type="audio/mpeg",
        headers=headers,
        background=cleanup,
    )

@nlp_router.post("/index/answer/feedback/{project_id}")
async def give_feedback(project_id: str, feedback_request: FeedbackRequest):
    answer_id = feedback_request.answer_id
    feedback = feedback_request.feedback

    if feedback not in [0, 1]:
        return {"error": "Feedback must be 0 or 1"}

    save_feedback(project_id, answer_id, feedback)

    return {"message": "Feedback saved successfully", "project_id": project_id, "answer_id": answer_id, "feedback": feedback}
