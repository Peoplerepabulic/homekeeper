#!/usr/bin/env python3
"""Generate 1280x720 title cards matching the HomeKeeper dark/Alexa-blue theme."""
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
BG_TOP = (22, 34, 74)      # #16224a
BG_BOT = (7, 11, 22)       # #070b16
ACCENT = (0, 200, 255)     # alexa cyan
WHITE = (238, 242, 255)
MUTED = (154, 167, 199)
WARN = (255, 176, 32)

REG = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
BOLD = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"


def gradient_bg():
    img = Image.new("RGB", (W, H), BG_BOT)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)
    # cyan glow band at top (Alexa light bar)
    for x in range(W):
        glow = max(0, 1 - abs(x - W / 2) / (W / 2))
        d.line([(x, 0), (x, 6)], fill=(0, int(119 * glow + 60), int(255 * glow + 60)))
    return img


def text_size(d, s, f):
    b = d.textbbox((0, 0), s, font=f)
    return b[2] - b[0], b[3] - b[1]


def centered(d, y, s, f, fill=WHITE):
    w, h = text_size(d, s, f)
    d.text(((W - w) / 2, y), s, font=f, fill=fill)
    return y + h


def card(lines, out):
    img = gradient_bg()
    d = ImageDraw.Draw(img)
    y = 150
    for text, size, color, gap in lines:
        f = ImageFont.truetype(BOLD if color in (WHITE,) else REG, size)
        y = centered(d, y, text, f, color) + gap
    img.save(out)
    print("wrote", out)


# ---- Card 1: problem intro ----
card([
    ("HomeKeeper", 96, WHITE, 34),
    ("Every appliance remembered. Every task on time.", 34, ACCENT, 60),
    ("The HVAC filter that was due two weeks ago.", 30, MUTED, 14),
    ("The water heater warranty, quietly expiring.", 30, MUTED, 14),
    ("The safety recall you never heard about — until it was too late.", 30, MUTED, 14),
], "/home/hatch/workspace/goals/amazon-developer-hackathon/homekeeper/video/cards/s1_title.png")

# ---- Card 2: closing ----
card([
    ("HomeKeeper", 96, WHITE, 34),
    ("A hot path under 500 ms with zero LLM cost,", 32, ACCENT, 8),
    ("and Bedrock judgment when it's actually needed.", 32, ACCENT, 50),
    ("MCP  ·  AWS Lambda  ·  Bedrock  ·  DynamoDB", 26, MUTED, 40),
    ("Every appliance remembered, every task on time.", 30, WHITE, 0),
], "/home/hatch/workspace/goals/amazon-developer-hackathon/homekeeper/video/cards/s2_close.png")
