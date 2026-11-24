import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

import clinic
from bot import run_bot

app = FastAPI()


@app.get("/api/doctors")
def doctors():
    return clinic.snapshot()[0]


@app.get("/api/appointments")
def appointments():
    return clinic.snapshot()[1]


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    await run_bot(websocket)


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "frontend", html=True))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
