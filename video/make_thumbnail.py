#!/usr/bin/env python3
"""Thumbnail: briefing screenshot center-cropped to 16:9 with a title band."""
from PIL import Image, ImageDraw, ImageFont

BASE = "/home/hatch/workspace/goals/amazon-developer-hackathon/homekeeper/video"
W, H = 1280, 720
BOLD = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
REG = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"

img = Image.open(f"{BASE}/shots/s3_briefing.png").convert("RGB")
# center-crop to 16:9
w, h = img.size
target = w / 16 * 9
if target <= h:
    top = int((h - target) / 2)
    img = img.crop((0, top, w, top + int(target)))
else:
    tw = int(h * 16 / 9)
    left = int((w - tw) / 2)
    img = img.crop((left, 0, left + tw, h))
img = img.resize((W, H), Image.LANCZOS)

d = ImageDraw.Draw(img, "RGBA")
# bottom gradient band
for y in range(430, H):
    a = int(200 * (y - 430) / (H - 430))
    d.rectangle([0, y, W, y + 1], fill=(7, 11, 22, a))
# top cyan strip
for x in range(W):
    glow = max(0, 1 - abs(x - W / 2) / (W / 2))
    d.line([(x, 0), (x, 5)], fill=(0, int(119 * glow + 60), int(255 * glow + 60), 255))

f_title = ImageFont.truetype(BOLD, 64)
f_sub = ImageFont.truetype(REG, 34)
t1 = "HomeKeeper — Demo Video"
b = d.textbbox((0, 0), t1, font=f_title)
d.text(((W - (b[2] - b[0])) / 2, 470), t1, font=f_title, fill=(255, 255, 255))
t2 = "Alexa+ hackathon · Simulated Echo Show, real MCP backend on AWS"
b2 = d.textbbox((0, 0), t2, font=f_sub)
d.text(((W - (b2[2] - b2[0])) / 2, 560), t2, font=f_sub, fill=(0, 200, 255))

out = f"{BASE}/thumbnail.png"
img.convert("RGB").save(out)
print("wrote", out)
