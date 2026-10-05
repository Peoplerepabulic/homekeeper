# HomeKeeper — Demo Video Script (under 3 minutes)

Live demo URL: https://d250h79d4lokny.cloudfront.net/
Label on screen: "Simulated Alexa+ experience · Real MCP backend on AWS"

## 0:00–0:20 — Hook
"Every appliance remembered. Every task on time. This is HomeKeeper — a home-appliance
manager built for the Alexa+ track. Since Alexa AI developer tools aren't available to us
yet, the Echo Show you see is a high-fidelity web simulator — but every card it renders
comes from a REAL MCP backend running on AWS: Lambda + FastMCP + DynamoDB, with Bedrock
Claude for chat and vision."

## 0:20–1:00 — Flow 1: Morning briefing (deterministic, <500ms)
Click "☀️ Morning Briefing".
"This calls the get_home_briefing MCP tool. Five items: one overdue filter change,
two due soon, one expiring warranty, one safety recall from the live CPSC feed.
385 milliseconds, straight from DynamoDB. Tap any row — that's get_appliance —
for brand, model, warranty, and maintenance history."

## 1:00–1:40 — Flow 2: Issue triage (deterministic, <500ms)
Click "🔧 Issue Triage".
"The water heater is making a strange noise. report_issue pulls the full picture —
device age, warranty status, last maintenance, open tasks — plus a knowledge-base note:
usually sediment buildup. And because it's still under warranty, the card recommends
going through the manufacturer's warranty process. Rule-based, shown as reference."

## 1:40–2:30 — Flow 3: Photo onboarding (real backend, Bedrock vision)
Click "📷 Photo Onboarding".
"start_photo_onboarding mints a one-time token and a QR code. Scan it with your phone,
photograph the appliance nameplate. The page polls the backend; when the photo lands in
S3, Bedrock Claude Vision extracts brand, model, serial number, and manufacture date —
each with a confidence score. You pick the category, hit confirm, and add_appliance
writes it to DynamoDB for real."

## 2:30–2:50 — Free ask (Bedrock + MCP tools)
Type: "When does the water heater warranty expire?"
"Free-form questions go to Bedrock Claude, which calls the same seven MCP tools.
It asks which water heater I mean, then answers from the real warranty record —
never invented."

## 2:50–3:00 — Close
"HomeKeeper: hot path under 500 milliseconds with zero LLM cost, cold path with
Bedrock when judgment is needed. Every appliance remembered, every task on time."

---
Total: ~2:50. Keep the browser devtools network tab visible for the latency numbers.
Warm up the Lambda once before recording (first cold start ~2s).
