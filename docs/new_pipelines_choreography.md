# Faceless Video Pipeline Choreography — Research & Design

## 1. CHAT ROAST (iPhone iMessage Style)

### Visual Choreography
1. **Header**: Contact name centered, "iMessage" blue dot, camera/phone icons top-right
2. **Filler history** (3-5 pre-written messages so the argument isn't the first thing)
   - "hey what's up"
   - "nm just at the gym u"
   - "same, about to finish this set"
3. **Vegeta is typing...** (3 bouncing gray dots, ~2 sec)
4. **Gray bubble slides in from left** with Vegeta's message
5. **Read receipt** appears under Vegeta's message (small gray "Read")
6. **Typing indicator** appears for Goku (2 sec)
7. **Blue bubble slides in from right** with Goku's message
8. **Delivered** -> **Read** receipt appears under Goku's message
9. **Repeat** for each exchange, scrolling up when screen fills

### Technical Details
- **Background**: Light gray (F2F2F7), not white
- **Sent bubbles**: System blue (#007AFF), right-aligned, rounded 18px
- **Received bubbles**: Gray (#E9E9EB), left-aligned, rounded 18px
- **Profile pics**: 28px circles, sprites from `data/sprites_clean/{goku,vegeta}.png`
- **Typing indicator**: 3 gray dots bouncing vertically, ~500ms per dot
- **Scroll**: Smooth CSS-style translate when y > viewport - 200
- **Timestamps**: Small gray text above bubble groups ("2:34 PM")
- **Send status**: "Delivered" → "Read" under sent messages
- **Bottom bar**: Camera icon, text input field (empty), Mic icon

### Audio Choreage
- **TTS voices**: Goku (en-US-GuyNeural → RVC goku.pth), Vegeta (en-US-SteffanNeural → RVC vegeta.pth)
- **Typing sound**: subtle tap-tap (optional)
- **Send/whoosh sound**: on bubble appear (optional)

### Prompt Template
```
Write a funny iMessage argument between {char1} and {char2} about {topic}.
Use texting language: bro, ngl, fr, tbh, lmao, wtf, idc, bruh, ratio.
Short bubbles (5-15 words max). Alternate speakers.
Start with light small talk, escalate to the roast.

Output JSON:
[
  {"speaker": "Goku", "text": "yo what are you up to"},
  {"speaker": "Vegeta", "text": "training, unlike you lol"},
  ...
]
```

---

## 2. X COMMENTS (Twitter/X Style)

### Visual Choreography
1. **Header**: "X" logo top-left, search icon top-right
2. **Original post** (the "seed" tweet):
   - Goku's avatar (sprite) + name + @goku handle
   - Post text (the initial take)
   - Timestamp (e.g., "2m")
   - Engagement bar: reply count, retweet count, like count
3. **Divider line** (thin gray)
4. **First reply pops up** (Vegeta):
   - Avatar + name + @vegeta handle
   - "Replying to @goku" in gray below the name
   - The reply text
   - Timestamp
   - Engagement bar
5. **Second reply pops up** (Goku responding to Vegeta):
   - Same format
   - "Replying to @vegeta"
6. **Repeat** for each reply, scrolling as thread grows

### Technical Details
- **Background**: Pure black (#000000)
- **Post container**: Full-width card
- **Avatar**: 40px circle, sprites
- **Name**: White, bold, 15px
- **Handle**: Gray (#71767B), 15px
- **Post text**: White, 15px, line-height 1.3
- **Replying-to**: Gray (#71767B), 13px, "Replying to @username"
- **Engagement bar**: Gray icons + counts at bottom
- **Dividers**: 1px #2F3336 lines between posts
- **Scroll**: Vertical scroll when thread > viewport

### Audio Choreography
- **TTS voices**: Goku (RVC goku), Vegeta (RVC vegeta)
- **Pop sound**: subtle "pop" on each new post appearing (optional)

### Prompt Template
```
Write a fake X comment thread argument between @Goku and @Vegeta about {topic}.
Use X language: ratio, L, W, based, cringe, no cap, fr, ngl.
Each reply should reference or mock the previous one.
First post is a take, then they argue in replies.

Output JSON:
[
  {"user": "Goku", "text": "take about topic", "is_reply": false},
  {"user": "Vegeta", "text": "mocking reply", "is_reply": true},
  ...
]
```

---

## 3. PHONE CALL (iPhone Incoming Call UI)

### Visual Choreography
1. **Incoming call screen** (first 3-4 seconds):
   - Large circular avatar (Vegeta sprite) centered
   - "Vegeta" name large below avatar
   - "mobile" label in gray below
   - "slide to answer" at bottom (or answer/decline buttons)
2. **Transition to active call**:
   - Avatar shrinks to top-left
   - Timer starts counting up (e.g., 0:00 → 0:01 → 0:02...)
   - Name + "mobile" centered at top
   - 6-button grid in center:
     - Speaker, Mute, Keypad, Add call, FaceTime, Contacts
   - Red "end call" button at bottom
3. **During call**:
   - Subtle waveform or audio visualization
   - Timer continues counting
   - Speaker label changes when one talks ("Goku" / "Vegeta" appearing)

### Technical Details
- **Background**: Dark gradient (like iPhone call screen)
- **Avatar**: 80-100px circle, sprite
- **Name**: White, 28px, centered
- **Timer**: White, monospace, counts up
- **Buttons**: Gray rounded squares, 60-70px, dark circles with icons
- **End call**: Red rounded rectangle at bottom

### Audio Choreage
- **Ringtone** during incoming (optional, ~2 sec)
- **TTS voices**: Goku (RVC goku), Vegeta (RVC vegeta)
- **Call connected sound**: "boop-boop" (optional)

### Prompt Template
```
Write a fake phone call transcript between Goku and Vegeta about {topic}.
Natural speech patterns: "um", "like", "uh", pauses.
One character calls the other.
Build tension through the call, end dramatic or funny.

Output JSON:
[
  {"speaker": "Goku", "text": "hey can we talk"},
  {"speaker": "Vegeta", "text": "yeah what do you want"},
  ...
]
```

---

## 4. STORYTIME (Slideshow with Transitions)

### Visual Choreage
1. **Title card** (2 sec):
   - "Storytime" in script/handwritten font
   - "with Goku" subtitle
   - Subtle animation (fade in)
2. **Slide 1** (image + narration):
   - Full-bleed image (from Pexels/Pixabay CC0, or sprite scene)
   - Text overlay at bottom (caption, white text with black backdrop)
   - Fade transition in (500ms)
   - Ken Burns zoom/pan effect (slow zoom in over 5-7 sec)
3. **Slide 2** (next image + narration):
   - Fade out Slide 1, Fade in Slide 2 (crossfade 500ms)
   - New text overlay
   - Ken Burns effect continues
4. **Repeat** for each slide
5. **End card** (2 sec):
   - "Like & Follow for more"
   - Or "The End" or "Moral of the story"

### Technical Details
- **Background**: Dark (#1a1a2e or #0f0f23)
- **Image**: 608x1080, full screen
- **Text overlay**: White, 24px, bottom 20% with semi-black gradient
- **Transitions**: Crossfade (500ms), Ken Burns (slow zoom 1.0→1.1)
- **Duration**: 5-8 seconds per slide
- **Image source**: 
  - Pexels API (free, CC0) for relevant images
  - Or sprites from `data/characters/` as characters in scenes
  - Fallback: solid color + character sprite

### Audio Choreage
- **Narrator**: RVC voice (Goku or whichever character is "telling" the story)
- **Background music**: Optional subtle lo-fi
- **Transition whoosh**: Optional on slide change

### Prompt Template
```
Write a short funny story from Goku's first-person perspective about {topic}.
Conversational, slightly exaggerated.
Include setup, buildup, and punchline ending.
Each paragraph should correspond to one slide.

Output JSON:
[
  {"text": "So I was at the gym the other day...", "image_query": "gym workout"},
  {"text": "and I saw Vegeta doing bicep curls...", "image_query": "vegeta gym"},
  {"text": "he was swinging so bad...", "image_query": "bad form gym"},
  {"text": "I couldn't stop laughing", "image_query": "laughing gym"}
]
```

---

## COMMON ELEMENTS

### Profile Pictures
- Use sprites from `data/characters/{name}/sprites/`
- Fallback: PIL-generated colored circles with initials

### Voice Consistency
- Goku: narrator en-US-GuyNeural → RVC goku.pth
- Vegeta: narrator en-US-SteffanNeural → RVC vegeta.pth
- Each voice has distinct pitch/timbre from RVC model

### Timing
- Chat Roast: ~30-40 sec (8-10 messages)
- X Comments: ~25-35 sec (6-8 replies)
- Phone Call: ~25-35 sec (6-8 exchanges)
- Storytime: ~30-45 sec (4-5 slides)

### Resolution/FPS
- 608x1080 (portrait, matches existing pipeline)
- 45 FPS

---

## RENDERING APPROACH

Instead of ffmpeg drawtext (which breaks on special chars and long filter strings), we should:

1. **Render frames as PNG images using PIL** (Python Image Library)
   - Each "state" is one frame
   - PIL handles text wrapping, colors, emoji rendering, anti-aliasing
2. **Use ffmpeg to stitch frames + audio into video**
   - `framerate` matches intended display speed
   - Concatenated audio file (WAV) synced to frame timing

### Frame Generation Strategy
- **Chat**: One frame per "state" (each bubble appearing, typing indicator, scroll)
- **X**: One frame per post appearing
- **Phone Call**: One frame per second (timer ticks)
- **Storytime**: One frame per image transition (crossfade)

### Audio Sync
- Generate each message's audio separately
- Concatenate with `concat` filter
- Frame rate matched to audio duration
