#!/usr/bin/env bash
# Assemble the HomeKeeper demo video:
#   per-section 1280x720 image looped for that section's audio duration, then
#   concatenated, with small burned-in subtitles.
# Run AFTER audio/*.mp3 and shots/*.png exist.
set -euo pipefail
V=~/workspace/goals/amazon-developer-hackathon/homekeeper/video
A=$V/audio; S=$V/shots; C=$V/cards; T=$V/tmp
mkdir -p "$T"

# section -> image (fallback to title card if a screenshot is missing)
declare -A IMG=(
  [s1_problem]="$C/s1_title.png"
  [s2_whatitis]="$S/s2_idle.png:$C/s1_title.png"
  [s3_briefing]="$S/s3_briefing.png:$C/s1_title.png"
  [s4_triage]="$S/s4_triage.png:$C/s1_title.png"
  [s5_onboarding]="$S/s5_onboarding.png:$C/s1_title.png"
  [s6_freeask]="$S/s6_freeask.png:$S/s2_idle.png"
  [s7_close]="$C/s2_close.png"
)

SECTIONS="s1_problem s2_whatitis s3_briefing s4_triage s5_onboarding s6_freeask s7_close"
> "$T/concat.txt"
for s in $SECTIONS; do
  first="${IMG[$s]%%:*}"; alt="${IMG[$s]#*:}"
  img="$first"; [ -f "$img" ] || img="$alt"
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$A/$s.mp3")
  # silent video of EXACTLY the audio duration (avoids -shortest padding drift)
  ffmpeg -y -v error -loop 1 -i "$img" -t "$dur" \
    -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=#070b16" \
    -c:v libx264 -preset medium -crf 20 -r 30 -pix_fmt yuv420p -an "$T/$s-v.mp4"
  ffmpeg -y -v error -i "$T/$s-v.mp4" -i "$A/$s.mp3" \
    -c:v copy -c:a aac -b:a 128k -shortest "$T/$s.mp4"
  echo "file '$T/$s.mp4'" >> "$T/concat.txt"
done

ffmpeg -y -v error -f concat -safe 0 -i "$T/concat.txt" -c copy "$T/joined.mp4"

# srt -> ass with explicit small-subtitle style (force_style on srt proved unreliable)
ffmpeg -y -v error -i "$V/subs.srt" "$V/subs.ass"
sed -i 's/^Style: Default,Arial,16,&Hffffff,&Hffffff,&H0,&H0,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1$/Style: Default,Noto Sans,22,\&H00FFFFFF,\&H000000FF,\&H80000000,\&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,24,1/' "$V/subs.ass"
# burn in subtitles, kept small per the user's request
ffmpeg -y -v error -i "$T/joined.mp4" \
  -vf "subtitles=$V/subs.ass" \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -c:a copy \
  "$V/homekeeper_demo.mp4"

ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 "$V/homekeeper_demo.mp4"
echo "FINAL: $V/homekeeper_demo.mp4"
