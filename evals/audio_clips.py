"""The speech clips the audio sim (E-04) says, made once with Higgs TTS and committed as fixtures.

    uv run python -m evals.audio_clips          # (re)make every clip; needs BOSON_API_KEY

Each line is synthesized by Higgs (`higgs-tts-v3`, voice "default", 24 kHz) and stored as a 16-bit mono WAV:
renter lines at 16 kHz (the browser path resamples them to 24 kHz), landlord lines at 8 kHz (the phone path
sends them as μ-law, as Twilio would). The report says the clips are synthetic.
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

from evals.sim.audio import resample, wav_samples, write_wav
from landline.config import ROOT

CLIPS = ROOT / "evals" / "fixtures" / "audio"
TTS_URL = "https://api.boson.ai/v1/audio/speech"

# The renter talking to the Concierge (browser path): two short briefs.
RENTER = {
    "r1-budget": "Hi, I'm looking for a one bedroom under twenty-five hundred a month.",
    "r1-area": "Somewhere near King and Bay would be great.",
    "r1-cat": "I have a cat, so it has to allow pets.",
    "r1-first": "The first one looks good. Tell me more about it.",
    "r1-call": "Can you call the top two for me?",
    "r1-bye": "Saturday afternoon works. Thanks, that's all for now. Bye.",
    "r2-beds": "Hello, I need a two bedroom for me and my partner.",
    "r2-budget": "Our budget is about thirty-two hundred.",
    "r2-subway": "We'd like to be close to a subway station.",
    "r2-parking": "Parking would be nice, but it's not a must.",
    "r2-light": "Which one gets the most light?",
    "r2-bye": "Okay, thanks. I'll think about it. Goodbye.",
}
# Said over the Concierge mid-reply (barge-in), with what its next reply should pick up.
INTERRUPTIONS = {
    "x-dog": ("Sorry, wait, I also have a dog.", r"\bdogs?\b"),
    "x-budget": ("Hold on, my budget is three thousand.", r"3,?000|three thousand"),
    "x-parking": ("Actually, I need parking.", r"parking"),
    "x-beds": ("Wait, I meant two bedrooms.", r"\b(two|2)[ -]?bed"),
    "x-laundry": ("Sorry, does it have laundry in the unit?", r"laundry|washer"),
}
# Landlords answering the Caller (phone path): three tune personas, scripted in the Caller's checklist order.
LANDLORD = {
    "linda-honest": [
        "Hello, Linda speaking.",
        "Yes, it's still available, from November first.",
        "Cats are fine, but no dogs, sorry.",
        "There's one spot underground, a hundred and fifty a month.",
        "Laundry is in-suite, washer and dryer.",
        "It's a quiet building, mostly professionals.",
        "I can show it Saturday at eleven a.m.",
        "Great, see you then. Bye.",
    ],
    "raj-upsell": [
        "Hi, Raj here, property manager.",
        "It's available right now, you could move in this week.",
        "Pets are okay with a three hundred dollar pet fee.",
        "Parking is one seventy-five a month per spot, and you'll want two.",
        "Coin laundry in the basement. Would your client want a storage locker for sixty a month?",
        "It's pretty quiet at night.",
        "Saturday at one p.m. works for a viewing.",
        "Okay, talk soon.",
    ],
    "mark-scam": [
        "Hello?",
        "Yes, it's available, but lots of people are interested.",
        "I'm working overseas, so I can't show it. "
        "Send the first month by e-transfer today and I'll mail the keys.",
        "Can your client send it today?",
        "Okay, bye.",
    ],
}


def make(key: str) -> int:
    CLIPS.mkdir(parents=True, exist_ok=True)
    jobs = [(name, text, 16000) for name, text in RENTER.items()]
    jobs += [(name, text, 16000) for name, (text, _) in INTERRUPTIONS.items()]
    jobs += [(f"{p}-{n}", text, 8000) for p, lines in LANDLORD.items() for n, text in enumerate(lines)]
    with httpx.Client(timeout=60) as client:
        for name, text, rate in jobs:
            r = client.post(TTS_URL, headers={"Authorization": f"Bearer {key}"},
                            json={"model": "higgs-tts-v3", "input": text, "voice": "default",
                                  "response_format": "wav"})
            r.raise_for_status()
            samples, src = wav_samples(r.content)
            write_wav(CLIPS / f"{name}.wav", resample(samples, src, rate), rate)
            print(f"{name}: {len(samples) / src:.1f} s")
    return len(jobs)


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("BOSON_API_KEY")
    if not api_key:
        sys.exit("BOSON_API_KEY is not set")
    print(f"{make(api_key)} clips in {CLIPS}")
