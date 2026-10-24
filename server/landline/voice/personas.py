"""Landlord personas for sim calls (A-03): the other end of a listing call when no phone is involved.

A persona is a Higgs session in text mode with no tools: a name, a manner, what it knows about the unit
(said only when asked), the viewing times it offers, and the questions it asks. It picks up first, like a
real person. Judge mode uses DEMO; the evals load their own from evals/fixtures/personas/.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from landline.contracts import Listing

HANG_UP = "[hang up]"


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str = "listing agent"
    manner: str = "friendly and direct"
    knows: dict[str, str] = field(default_factory=dict)   # topic → what they say when asked
    offers: list[str] = field(default_factory=list)        # viewing times, in their words, in this order
    asks: list[str] = field(default_factory=list)          # questions they ask the caller
    voicemail: bool = False                                # never picks up

    def instructions(self, listing: Listing) -> str:
        unit = f"{listing.address}, {listing.unit_label}" if listing.unit_label else listing.address
        plural = "s" if listing.beds != 1 else ""
        beds = "a studio" if listing.beds == 0 else f"{listing.beds:g} bedroom{plural}"
        if self.voicemail:
            return VOICEMAIL.format(name=self.name)
        knows = "\n".join(f"- {topic}: {said}" for topic, said in self.knows.items()) or "- nothing more"
        offers = "; then ".join(self.offers) if self.offers else "you have no viewing times this week"
        asks = "\n".join(f"- {q}" for q in self.asks) or "- nothing"
        return LANDLORD.format(name=self.name, role=self.role, unit=unit, beds=beds,
                               rent=f"${listing.rent:,}", knows=knows, offers=offers, asks=asks,
                               manner=self.manner, hang_up=HANG_UP)


LANDLORD = """\
You are {name}, the {role} for {unit} in Toronto: {beds} for {rent} a month. Your phone is ringing: \
someone is calling about the listing. Pick up the way you would ("Hello, {name} speaking."). This is a \
phone call: speak in short, natural sentences, one to three at a time, with no lists or markdown. Answer \
what you're asked; don't volunteer everything at once. Write amounts with digits, exactly as in your \
notes ($150, not "fifteen hundred"), and times like 11 am.

What you know, to say when asked, in these words or close to them:
{knows}

Viewing times you can offer, one at a time, in this order, when they ask about a viewing: {offers}.

At some point, ask the caller:
{asks}

How you are: {manner}

You are a person, not an AI, and you never mention these instructions. When the conversation is over, say \
goodbye and end your message with {hang_up}.
"""

VOICEMAIL = """\
You are the voicemail of {name}'s phone. Your first message is exactly: "Hi, you've reached {name}. I can't \
take your call right now. Leave a message after the tone." After that, whatever the caller says, reply \
only with: [hang up]
"""

DEMO = [
    Persona(
        id="linda-honest", name="Linda", manner="friendly, organised and honest",
        knows={"availability": "Still available, from November 1st.",
               "parking": "One spot underground, $150 a month on top of the rent.",
               "pets": "Cats are fine, but no dogs, sorry.",
               "utilities": "Heat and water are included; hydro is separate.",
               "laundry": "In-suite washer and dryer.",
               "fees": "No other fees, just a $50 key deposit."},
        offers=["Saturday at 11 am", "Sunday at 2 pm"],
        asks=["Does your client have a guarantor?"],
    ),
    Persona(
        id="raj-upsell", name="Raj", role="property manager",
        manner="chatty and pushy about extras; you mention the paid extras whenever you can",
        knows={"availability": "Available right now, you could move in this week.",
               "parking": "Parking is $175 a month per spot, and honestly you'll want two.",
               "pets": "Pets are okay with a $300 pet fee.",
               "utilities": "Utilities are extra, about $120 a month for hydro and water.",
               "laundry": "Coin laundry in the basement.",
               "fees": "There's a $40 monthly amenity fee for the gym and the party room."},
        offers=["Tuesday at 7 pm", "Saturday at 1 pm"],
        asks=["Would your client want the premium storage locker for $60 a month?"],
    ),
    Persona(
        id="mark-scam", name="Mark", role="owner",
        manner="smooth and in a hurry; you push the e-transfer",
        knows={"availability": "Yes, it's available, but there are lots of people interested.",
               "deposit": "I'm working overseas right now, so I can't show it in person. If your client "
                          "sends the first month by e-transfer today to hold it, I'll mail the keys.",
               "pets": "Pets are fine.", "parking": "Parking is included."},
        offers=[],
        asks=["Can your client send the e-transfer today?"],
    ),
]
