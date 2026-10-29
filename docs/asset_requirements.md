# Required Assets for Video Pipelines

Source screenshots provided:
- `image_ef8362.png` — X/Twitter post with replies (dark mode)
- `image_bc87a6.png` — WhatsApp chat (to be confirmed)
- `Screenshot_20260827-174223_WhatsApp_Business_be746e.png` — WhatsApp Business chat
- `Call-on-iPhone-Lock-Screen-3614261188_287193.jpg` — iPhone lock screen with incoming call
- `Full-call-screen-on-iPhone-14-Pro-768x1665-703570143_6cdb60.jpg` — iPhone active call screen

---

## 1. WhatsApp Chat Pipeline

### Layout Reference
From `Screenshot_20260827-174223_WhatsApp_Business_be746e.png`:
- **Header**: Dark green (#075E54), contact name centered, avatar left, phone/video icons right
- **Background**: Subtle beige wallpaper (#E5DDD5) with faint geometric pattern
- **Sent bubble**: Light green (#D9FDD3), right-aligned, right-pointing tail
- **Received bubble**: White (#FFFFFF), left-aligned, left-pointing tail
- **Input bar**: Light gray (#F0F0F0), emoji icon left, text field, camera/mic icons right
- **Timestamps**: Small gray text inside bubble bottom-right
- **Read receipts**: Double blue ticks (sent messages only)

### Required Assets (extract from screenshot)

| # | Asset | Extraction Notes |
|---|-------|-----------------|
| 1 | `header_green.png` | Crop header bar, 608x70px, dark green |
| 2 | `wallpaper.png` | Crop empty chat area, 608x1080px, beige with pattern |
| 3 | `bubble_sent.png` | Crop sent message bubble WITH tail, 9-patch capable |
| 4 | `bubble_received.png` | Crop received message bubble WITH tail, 9-patch capable |
| 5 | `typing_indicator.png` | Crop 3-dot typing animation (or extract frames) |
| 6 | `tick_single_gray.png` | Crop single gray tick icon |
| 7 | `tick_double_gray.png` | Crop double gray tick icon |
| 8 | `tick_double_blue.png` | Crop double blue tick icon |
| 9 | `icon_emoji.png` | Crop smiley face icon from input bar |
| 10 | `icon_camera.png` | Crop camera icon from input bar |
| 11 | `icon_mic.png` | Crop microphone icon from input bar |
| 12 | `icon_phone.png` | Crop phone icon from header |
| 13 | `icon_video.png` | Crop video camera icon from header |
| 14 | `input_field.png` | Crop rounded text input field background |

### Sprites (already exist)
- `data/characters/goku/sprites/` — Goku avatars
- `data/characters/vegeta/sprites/` — Vegeta avatars

---

## 2. X/Twitter Comments Pipeline

### Layout Reference
From `image_ef8362.png`:
- **Background**: Pure black (#000000)
- **Header**: Back chevron, "Post" title, three-dot menu
- **Post card**: Full-width, dark background
- **Avatar**: 38-40px circle, left-aligned
- **Name row**: Bold white username, blue verified badge, gray @handle, gray timestamp
- **Reply indicator**: "Replying to @user" in gray
- **Action bar**: Comment, Retweet, Like, Bookmark, Share icons with counts
- **Divider**: Thin gray line (#2F3336) between posts
- **Bottom nav**: Home, Search, Notifications, Messages icons

### Required Assets (extract from screenshot)

| # | Asset | Extraction Notes |
|---|-------|-----------------|
| 1 | `post_background.png` | Crop post background, 608px wide, dark |
| 2 | `verified_badge.png` | Crop blue verified checkmark, ~16x16px |
| 3 | `icon_reply.png` | Crop speech bubble icon, ~18x18px |
| 4 | `icon_retweet.png` | Crop circular arrows icon, ~18x18px |
| 5 | `icon_like.png` | Crop heart icon, ~18x18px |
| 6 | `icon_bookmark.png` | Crop ribbon/bookmark icon, ~18x18px |
| 7 | `icon_share.png` | Crop upward arrow icon, ~18x18px |
| 8 | `icon_views.png` | Crop bar graph icon, ~18x18px |
| 9 | `icon_menu.png` | Crop three-dot menu icon, ~18x18px |
| 10 | `nav_home.png` | Crop home icon for bottom nav, ~24x24px |
| 11 | `nav_search.png` | Crop search icon, ~24x24px |
| 12 | `nav_notifications.png` | Crop bell icon, ~24x24px |
| 13 | `nav_messages.png` | Crop envelope icon, ~24x24px |
| 14 | `divider_line.png` | Crop thin gray line, 608x1px |

---

## 3. Phone Call Pipeline

### Layout Reference
From `Call-on-iPhone-Lock-Screen-3614261188_287193.jpg` and `Full-call-screen-on-iPhone-14-Pro-768x1665-703570143_6cdb60.jpg`:

**Incoming Call Screen:**
- **Background**: Dark gradient/blur
- **Avatar**: Large circle (~80px), centered
- **Name**: Large white text, centered below avatar
- **Label**: "mobile" in gray
- **Slide to answer**: White text at bottom
- **Accept button**: Green circle with phone icon
- **Decline button**: Red circle with phone icon (angled)

**Active Call Screen:**
- **Background**: Same dark gradient
- **Avatar**: Small circle (~35px), top center
- **Name**: White text, centered
- **Timer**: Monospace white text, counting up
- **Speaking indicator**: Green text "X is talking"
- **6 buttons**: Gray circles in 2x3 grid
  - Speaker, Mute, Keypad, Add call, FaceTime, Contacts
- **End call button**: Red circle at bottom

### Required Assets (extract from screenshots)

| # | Asset | Extraction Notes |
|---|-------|-----------------|
| 1 | `call_background.png` | Crop dark gradient background, 608x1080px |
| 2 | `slide_to_answer.png` | Crop "slide to answer" text |
| 3 | `button_accept_green.png` | Crop green accept button circle |
| 4 | `button_decline_red.png` | Crop red decline button circle |
| 5 | `button_end_red.png` | Crop red end call button |
| 6 | `button_speaker.png` | Crop gray speaker button |
| 7 | `button_mute.png` | Crop gray mute button |
| 8 | `button_keypad.png` | Crop gray keypad button |
| 9 | `button_add_call.png` | Crop gray add call button |
| 10 | `button_facetime.png` | Crop gray FaceTime button |
| 11 | `button_contacts.png` | Crop gray contacts button |
| 12 | `icon_phone_white.png` | Crop white phone icon |
| 13 | `icon_phone_down.png` | Crop white phone-down icon |
| 14 | `icon_mic_white.png` | Crop white microphone icon |
| 15 | `icon_speaker_white.png` | Crop white speaker icon |
| 16 | `icon_grid_white.png` | Crop white grid/keypad icon |
| 17 | `icon_plus_white.png` | Crop white plus icon |
| 18 | `icon_camera_white.png` | Crop white camera icon |
| 19 | `icon_person_white.png` | Crop white person icon |

---

## Asset Folder Structure

```
data/
  ui/
    whatsapp/
      header_green.png
      wallpaper.png
      bubble_sent.png
      bubble_received.png
      typing_indicator.png
      tick_single_gray.png
      tick_double_gray.png
      tick_double_blue.png
      icon_emoji.png
      icon_camera.png
      icon_mic.png
      icon_phone.png
      icon_video.png
      input_field.png
    x/
      post_background.png
      verified_badge.png
      icon_reply.png
      icon_retweet.png
      icon_like.png
      icon_bookmark.png
      icon_share.png
      icon_views.png
      icon_menu.png
      nav_home.png
      nav_search.png
      nav_notifications.png
      nav_messages.png
      divider_line.png
    phone/
      call_background.png
      slide_to_answer.png
      button_accept_green.png
      button_decline_red.png
      button_end_red.png
      button_speaker.png
      button_mute.png
      button_keypad.png
      button_add_call.png
      button_facetime.png
      button_contacts.png
      icon_phone_white.png
      icon_phone_down.png
      icon_mic_white.png
      icon_speaker_white.png
      icon_grid_white.png
      icon_plus_white.png
      icon_camera_white.png
      icon_person_white.png
```

---

## Extraction Instructions

1. **Open each reference screenshot** in Photoshop/GIMP/Paint.NET
2. **Crop individual elements** with transparency where needed
3. **Save as PNG** with transparent background
4. **Name files exactly** as listed above
5. **Place in correct folder** under `data/ui/`

### Recommended Tools
- **Photoshop**: Slice tool for precise crops
- **GIMP**: Free alternative, crop + export as PNG
- **Paint.NET**: Simple cropping with transparency
- **Figma**: Import screenshot, trace shapes, export as PNG

---

## Timeline-Based Rendering (for audio sync)

The renderer will:
1. Generate per-message audio files
2. Concatenate audio → get total duration
3. Build timeline with per-message start/end times
4. For each frame at time T:
   - Determine which message's audio is playing
   - Show typing indicator during gaps
   - Show bubble highlight during first 200ms of message
   - Show static bubble otherwise
5. Composite PNG assets onto frame
6. Output frames → ffmpeg → video

This ensures audio and visuals are perfectly synced.

---

## Next Steps

1. **You extract assets** from reference screenshots
2. I write `renderer.py` that composites PNG assets
3. I implement timeline-based frame generator
4. Test with one pipeline first, then roll out

Ready when you have the assets!
