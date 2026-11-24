"""Wire format between the browser and Pipecat.
Binary message = raw PCM16 mono audio (browser -> server at 16 kHz, server -> browser at 24 kHz).
Text message   = JSON events (transcripts, bookings, interruptions)."""
import json

from pipecat.frames.frames import (Frame, InputAudioRawFrame, InterruptionFrame,
                                   OutputAudioRawFrame, OutputTransportMessageFrame,
                                   OutputTransportMessageUrgentFrame)
from pipecat.serializers.base_serializer import FrameSerializer

IN_RATE, OUT_RATE = 16000, 24000


class BrowserSerializer(FrameSerializer):
    async def serialize(self, frame: Frame):
        if isinstance(frame, InterruptionFrame):
            return json.dumps({"type": "interrupt"})
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message)
        return None

    async def deserialize(self, data):
        if isinstance(data, bytes):
            return InputAudioRawFrame(audio=data, sample_rate=IN_RATE, num_channels=1)
        return None
