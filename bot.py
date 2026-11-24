"""Voice pipeline: browser audio -> ElevenLabs STT -> Gemini (with tools) -> ElevenLabs TTS -> browser."""
import datetime as dt
import os

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (LLMFullResponseEndFrame, LLMRunFrame, LLMTextFrame,
                                   OutputTransportMessageUrgentFrame, TranscriptionFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (LLMContextAggregatorPair,
                                                                    LLMUserAggregatorParams)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

import clinic
from serializer import IN_RATE, OUT_RATE, BrowserSerializer

SYSTEM = """You are Aria, the phone receptionist at Meridian Clinic. Today is {today}.
You help callers check a doctor's availability, book an appointment, or cancel one.

Style: this is a phone call. Speak in short, warm sentences. No markdown, lists or emojis.
Say dates naturally (for example "Wednesday the twenty-fifth at ten in the morning").
Offer at most three slots at a time.

Identity: you only know the caller by the phone number they say out loud. Ask for it before booking or cancelling.
Read it back digit by digit and wait for a clear yes. Only then call the tool with phone_confirmed=true.
If they correct you, read the new number back again. Also ask for the caller's name before booking.

Flow: find out the doctor or speciality (and day if they care), call check_doctor_availability,
offer slots, confirm the chosen slot, then book_appointment. For cancellation, confirm the number,
then call cancel_appointment. If a tool returns an error, explain it plainly and fix it with the caller.
Never invent doctors or slots; use only tool results. Use dates in YYYY-MM-DD when calling tools."""

TOOLS = ToolsSchema(standard_tools=[
    FunctionSchema(
        name="check_doctor_availability",
        description="List free appointment slots, optionally filtered by doctor name, speciality or date.",
        properties={"doctor": {"type": "string", "description": "Doctor name, e.g. 'Rao'"},
                    "speciality": {"type": "string", "description": "e.g. Cardiology"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"}},
        required=[]),
    FunctionSchema(
        name="book_appointment",
        description="Book a slot for the caller. Only call after the caller confirmed their phone number.",
        properties={"slot_id": {"type": "string", "description": "slot_id from check_doctor_availability"},
                    "patient_name": {"type": "string"},
                    "phone": {"type": "string", "description": "Digits only, as spoken by the caller"},
                    "phone_confirmed": {"type": "boolean", "description": "True only after the caller said yes to the read-back"}},
        required=["slot_id", "patient_name", "phone", "phone_confirmed"]),
    FunctionSchema(
        name="cancel_appointment",
        description="Cancel the caller's active appointment, found by phone number.",
        properties={"phone": {"type": "string", "description": "Digits only"},
                    "phone_confirmed": {"type": "boolean"}},
        required=["phone", "phone_confirmed"]),
])


class Tap(FrameProcessor):
    """Forwards transcripts to the browser as JSON events."""

    def __init__(self):
        super().__init__()
        self._buf = ""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        msg = None
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            msg = {"type": "transcript", "role": "user", "text": frame.text.strip()}
        elif isinstance(frame, LLMTextFrame):
            self._buf += frame.text
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._buf.strip():
                msg = {"type": "transcript", "role": "bot", "text": self._buf.strip()}
            self._buf = ""
        await self.push_frame(frame, direction)
        if msg:
            await self.push_frame(OutputTransportMessageUrgentFrame(message=msg))


async def run_bot(websocket):
    transport = FastAPIWebsocketTransport(websocket=websocket, params=FastAPIWebsocketParams(
        audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False,
        serializer=BrowserSerializer()))

    stt = ElevenLabsRealtimeSTTService(api_key=os.environ["ELEVENLABS_API_KEY"])
    tts = ElevenLabsTTSService(api_key=os.environ["ELEVENLABS_API_KEY"],
                               voice_id=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"))
    llm = GoogleLLMService(api_key=os.environ["GOOGLE_API_KEY"],
                           model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                           system_instruction=SYSTEM.format(today=dt.date.today().strftime("%A, %Y-%m-%d")))

    async def notify(params, event, result):
        if result.get("ok"):
            await params.llm.push_frame(OutputTransportMessageUrgentFrame(
                message={"type": event, "appointment": result["appointment"]}))
        await params.result_callback(result)

    async def h_check(p):
        a = p.arguments
        await p.result_callback(clinic.check_availability(a.get("doctor"), a.get("speciality"), a.get("date")))

    async def h_book(p):
        a = p.arguments
        await notify(p, "booked", clinic.book(a.get("slot_id"), a.get("patient_name", ""),
                                              a.get("phone", ""), bool(a.get("phone_confirmed"))))

    async def h_cancel(p):
        a = p.arguments
        await notify(p, "cancelled", clinic.cancel(a.get("phone", ""), bool(a.get("phone_confirmed"))))

    llm.register_function("check_doctor_availability", h_check)
    llm.register_function("book_appointment", h_book)
    llm.register_function("cancel_appointment", h_cancel)

    context = LLMContext(messages=[{"role": "user", "content": "The caller just connected. Greet them briefly."}],
                         tools=TOOLS)
    agg = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer()))

    pipeline = Pipeline([transport.input(), stt, Tap(), agg.user(), llm, Tap(), tts,
                         transport.output(), agg.assistant()])
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=IN_RATE, audio_out_sample_rate=OUT_RATE))

    @transport.event_handler("on_client_connected")
    async def _on_connect(_t, _c):
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnect(_t, _c):
        await task.cancel()

    await PipelineRunner(handle_sigint=False).run(task)
