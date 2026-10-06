# HomeKeeper — Demo Video Script (pitch style, under 3 minutes)

Live demo URL: https://d250h79d4lokny.cloudfront.net/
On-screen label: "Simulated Alexa+ experience · Real MCP backend on AWS"
Warm up the Lambda once before recording (cold start ~2s, warm 13–76ms).

## 0:00–0:30 — The problem
"Your home is full of things you're supposed to remember and don't. The HVAC filter
that was due two weeks ago. The water heater warranty quietly expiring. The safety
recall you never heard about until it was too late. Appliances don't remind you —
and the cost of forgetting is real: breakdowns, voided warranties, safety risks.

HomeKeeper fixes this. Every appliance remembered. Every task on time."

## 0:30–0:50 — What it is, who it's for, how it uses the track's tool
"HomeKeeper is a home-appliance manager built for the Alexa+ track, for homeowners
and renters who'd rather ask than remember.

One honest note: Alexa's developer tools weren't available to our account — the
Alexa AI CLI path is gated behind an Amazon-internal allowlist — so the Echo Show
you see is a high-fidelity web simulator. But everything it shows comes from a
REAL backend on AWS: seven MCP tools on Lambda + DynamoDB, with Bedrock Claude
for chat and vision. Simulated Alexa+ experience, real MCP backend — it's written
right on the screen."

## 0:50–1:30 — The working app, flow 1: Morning briefing
"Here's the app working. 'Alexa, what needs attention at home today?'

One MCP call — get_home_briefing — and there's the whole household: an overdue
filter change, two tasks due soon, a warranty expiring in 20 days, and a live
safety recall pulled from the CPSC feed every morning at 6 AM. 365 milliseconds,
straight from DynamoDB — no LLM, no guessing. Tap any row and get_appliance shows
brand, model, warranty, full maintenance history."

## 1:30–2:00 — Flow 2: Issue triage
"'Alexa, the water heater is making a strange noise.'

report_issue pulls the complete picture — five years old, still under warranty
until October 24th, last maintenance on record — plus a knowledge-base note:
usually sediment buildup, and flushing the tank often fixes it. Because it's
in warranty, the card recommends going through the manufacturer's warranty
process. Deterministic, under half a second, rule-based and labeled as such."

## 2:00–2:40 — Flow 3: Photo onboarding
"'Alexa, I want to add a new appliance by photo.'

start_photo_onboarding mints a one-time token and a QR code. Scan it with your
phone, photograph the nameplate. The moment the photo lands in S3, Bedrock
Claude Vision extracts the brand, model, serial number, and manufacture date —
each with a confidence score. Pick the category, hit confirm, and add_appliance
writes it to DynamoDB for real. A thirty-second task that used to mean digging
through a filing cabinet."

## 2:40–2:55 — Free ask
"And for anything else, just ask. 'When does the water heater warranty expire?'
Bedrock calls the same MCP tools, asks which water heater I mean, and answers
from the real warranty record — never invented."

## 2:55–3:00 — Close
"HomeKeeper: a hot path under 500 milliseconds with zero LLM cost, and Bedrock
judgment when it's actually needed. Every appliance remembered, every task on
time."

---
Built With: MCP (FastMCP), AWS Lambda, Amazon Bedrock, DynamoDB, API Gateway,
EventBridge, S3, CloudFront. Track: Alexa+ (simulated Echo Show client).
