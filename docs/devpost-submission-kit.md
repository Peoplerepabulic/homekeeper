# HomeKeeper — Devpost Submission Kit

Fill order: upload video to YouTube (Public) first, then paste the sections below.

## 1. Video
- Upload `docs/video/homekeeper-demo-v3.mp4` to YouTube as **Public** (v3 = re-recorded on the live Galanz-seed backend with English voiceover; v1 shows the retired Samsung seed)
- Suggested title: `HomeKeeper — Alexa+ Home Appliance Manager | Build, Ship, Shape Hackathon`
- Suggested description:
  ```
  HomeKeeper: every appliance remembered, every task on time.
  A home-appliance manager for the Alexa+ track — self-hosted MCP server
  (spec 2025-11-25, Streamable HTTP) on AWS, with a simulated Echo Show frontend.
  Live demo: https://d250h79d4lokny.cloudfront.net/
  Code: https://github.com/Peoplerepabulic/homekeeper
  Built for Build, Ship, Shape: Amazon Developer Hackathon.
  ```

## 2. Project description (Devpost "About")

```markdown
HomeKeeper is a home-appliance manager for the Alexa+ track: it remembers every
appliance's model, maintenance schedule, warranty, and recall status — and tells
you what needs attention before you have to ask.

**Track fit.** The Alexa+ track asks for a self-hosted MCP server (spec
2025-11-25+, Streamable HTTP) or a simulated Alexa+ experience. HomeKeeper is
both: the backend is a native MCP server (FastMCP, protocol 2025-11-25,
Streamable HTTP on API Gateway → Lambda); the frontend is a high-fidelity Echo
Show web simulator used for the demo video. Every card it renders comes from a
live MCP tool call. (Alexa's add-on toolchain is partner-only with no
self-serve access path, which the track rules anticipate by explicitly allowing
a simulated experience.)

**Architecture: hot path / cold path.** The hot path (every interaction) is
simulator → API Gateway → Lambda (FastMCP, stateless) → DynamoDB. No LLM on this
path — warm latency 13–76 ms, target < 500 ms. The cold path runs daily at
6:00 AM CT via EventBridge: a Lambda pulls the live CPSC safety-recall feed and
caches results in DynamoDB for the next briefing. Amazon Bedrock (Claude)
handles free-ask chat with MCP tool use and nameplate vision extraction.

**Seven MCP tools:** get_home_briefing, get_appliance, add_appliance,
start_photo_onboarding, log_maintenance, get_reorder_options, report_issue.

**Demo flows (see video):**
1. Morning Briefing — one MCP call, three items: overdue, expiring
   warranty, safety recall (real Galanz #10885, pulled from the live CPSC feed).
2. Issue Triage — "the water heater is making a strange noise" → warranty
   status, maintenance history, knowledge-base note, rule-based recommendation.
3. Photo Onboarding — QR code → phone photographs the nameplate → Bedrock
   vision extracts brand/model/serial with confidence scores → confirmed into
   DynamoDB. Verified end-to-end with a real Sharp microwave nameplate.
4. Free ask — Bedrock calls the same MCP tools; answers from real records,
   never invented.

**Design decisions:** the hot/cold split is the only way to guarantee
sub-500 ms responses; a static appliance catalog (not the LLM) is the source
of maintenance schedules — a hallucination guard; tools are coarse-grained and
intent-based so a conversational front end picks the right one.
```

## 3. Built With (Devpost "Built With" section — list each)

Alexa+, MCP (Model Context Protocol / FastMCP), AWS Lambda, Amazon Bedrock,
Amazon DynamoDB, Amazon API Gateway, Amazon EventBridge, Amazon S3, Amazon
CloudFront, Python, JavaScript

## 4. Product Feedback

Paste from `docs/product-feedback.md` (already written, English, per-tool).
Keep the friction-log honesty — it is a judging input, not just a formality.

## 5. Track / challenges

- Primary track: **Alexa+**
- Mini challenge: **AWS Builder**
- (Open Source: repo is MIT-licensed and public; enter only if the rules allow
  stacking with the above.)

## 6. Final checklist

- [ ] YouTube video is Public and plays
- [ ] Devpost description pasted
- [ ] Built With lists every tool above
- [ ] Product Feedback pasted per question
- [ ] GitHub repo link correct and public
- [ ] Page shows **Submitted**, not Draft
- [ ] Submitted by internal target 2026-10-22 (deadline 2026-10-23 12:00 PM PT)
