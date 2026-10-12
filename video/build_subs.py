#!/usr/bin/env python3
"""Build video/subs.srt: distribute each section's subtitle lines proportionally
by character count across that section's measured audio duration."""
import json, subprocess, os

BASE = "/home/hatch/workspace/goals/amazon-developer-hackathon/homekeeper/video"
AUDIO = os.path.join(BASE, "audio")

# subtitle lines per section (written form, matches narration)
LINES = {
    "s1_problem": [
        "Your home is full of things you're supposed to remember and don't.",
        "The HVAC filter that was due two weeks ago.",
        "The water heater warranty quietly expiring.",
        "The safety recall you never heard about until it was too late.",
        "Appliances don't remind you — and the cost of forgetting is real:",
        "breakdowns, voided warranties, safety risks.",
        "HomeKeeper fixes this. Every appliance remembered. Every task on time.",
    ],
    "s2_whatitis": [
        "HomeKeeper is a home-appliance manager built for the Alexa+ track,",
        "for homeowners and renters who'd rather ask than remember.",
        "One honest note: Alexa's developer tools weren't available to our account —",
        "the Alexa AI CLI path is gated behind an Amazon-internal allowlist —",
        "so the Echo Show you see is a high-fidelity web simulator.",
        "But everything it shows comes from a REAL backend on AWS:",
        "seven MCP tools on Lambda + DynamoDB, with Bedrock Claude for chat and vision.",
        "Simulated Alexa+ experience, real MCP backend — it's written right on the screen.",
    ],
    "s3_briefing": [
        "Here's the app working. “Alexa, what needs attention at home today?”",
        "One MCP call — get_home_briefing — and there's the whole household:",
        "an overdue filter change, two tasks due soon,",
        "a warranty expiring in 20 days, and a live safety recall",
        "pulled from the CPSC feed every morning at 6 AM.",
        "365 milliseconds, straight from DynamoDB — no LLM, no guessing.",
        "Tap any row and get_appliance shows brand, model, warranty, full maintenance history.",
    ],
    "s4_triage": [
        "“Alexa, the water heater is making a strange noise.”",
        "report_issue pulls the complete picture —",
        "five years old, still under warranty until October 24th, last maintenance on record —",
        "plus a knowledge-base note: usually sediment buildup, and flushing the tank often fixes it.",
        "Because it's in warranty, the card recommends going through the manufacturer's warranty process.",
        "Deterministic, under half a second, rule-based and labeled as such.",
    ],
    "s5_onboarding": [
        "“Alexa, I want to add a new appliance by photo.”",
        "start_photo_onboarding mints a one-time token and a QR code.",
        "Scan it with your phone, photograph the nameplate.",
        "The moment the photo lands in S3, Bedrock Claude Vision extracts",
        "the brand, model, serial number, and manufacture date — each with a confidence score.",
        "Pick the category, hit confirm, and add_appliance writes it to DynamoDB for real.",
        "A thirty-second task that used to mean digging through a filing cabinet.",
    ],
    "s6_freeask": [
        "And for anything else, just ask.",
        "“When does the water heater warranty expire?”",
        "Bedrock calls the same MCP tools, asks which water heater I mean,",
        "and answers from the real warranty record — never invented.",
    ],
    "s7_close": [
        "HomeKeeper: a hot path under 500 milliseconds with zero LLM cost,",
        "and Bedrock judgment when it's actually needed.",
        "Every appliance remembered, every task on time.",
    ],
}

SECTIONS = ["s1_problem", "s2_whatitis", "s3_briefing", "s4_triage",
            "s5_onboarding", "s6_freeask", "s7_close"]


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", path], capture_output=True, text=True)
    return float(json.loads(out.stdout)["format"]["duration"])


def ts(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    durs = {}
    for s in SECTIONS:
        p = os.path.join(AUDIO, s + ".mp3")
        durs[s] = duration(p)
        print(s, f"{durs[s]:.1f}s")
    total = sum(durs.values())
    print("total audio:", f"{total:.1f}s", f"({total/60:.2f} min)")

    entries = []
    t = 0.0
    for s in SECTIONS:
        lines = LINES[s]
        weights = [max(len(x), 1) for x in lines]
        wsum = sum(weights)
        # leave 0.25s gap at section end
        budget = max(durs[s] - 0.25, 0.5)
        cur = t
        for i, line in enumerate(lines):
            span = budget * weights[i] / wsum
            start = cur
            end = cur + span
            if i == len(lines) - 1:
                end = min(end, t + budget)
            entries.append((start, end, line))
            cur = end + 0.02
        t += durs[s]

    out = os.path.join(BASE, "subs.srt")
    with open(out, "w") as f:
        for i, (a, b, line) in enumerate(entries, 1):
            f.write(f"{i}\n{ts(a)} --> {ts(b)}\n{line}\n\n")
    print("wrote", out, len(entries), "entries")


if __name__ == "__main__":
    main()
