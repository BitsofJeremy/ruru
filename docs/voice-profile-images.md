# Adding Profile Images to Voices

This guide explains how to add profile images to the built-in  voices (and any other voice) in WeirDing.

---

## Short Answer

You **do not need to change `characters_data.py`** to add profile images. That file only defines voice audio/text properties. Profile images are managed separately through the database and can be added two ways:

1. **Web UI** (easiest) — upload an image directly from the Voices page
2. **Bulk seed** (developer) — place images in `_voices/` and extend `seed_voices.py` to auto-load them

---

## Option 1: Upload via the Web UI

1. Go to the **Voices** page in the sidebar.
2. Click the voice card you want to update (e.g., Pixel Paradox).
3. On the voice detail/edit page, use the **Upload Profile Image** button.
4. Select a PNG or JPG file (recommended: square crop, at least 256×256 px).
5. Save — the image appears immediately on the voice card.

This works for both built-in  voices and any voice you've cloned or designed.

---

## Option 2: Bulk-seed images for  voices

Use this if you want profile images to be automatically set the **first time** the app seeds the database (fresh install), or want to re-seed by clearing the voices table.

### Step 1 — Prepare image files

Place one image per character in the `_voices/` directory at the repo root, named exactly `{slug}.png` (or `{slug}.jpg`):

```
_voices/
  arc.wav
  a1.png           ← profile image for A1
  baron.wav
  baron.png
  clive.wav
  clive.png
  glitch.wav
  glitch.png
  kai.wav
  kai.png
  lumina.wav
  lumina.png
  meatball.wav
  meatball.png
  nano.wav
  nano.png
  nocturne.wav
  nocturne.png
  pixel.wav
  pixel.png
```

### Step 2 — Edit `models/seed_voices.py`

Open `models/seed_voices.py` and update the seeding loop to also copy and register the image.
Find the section that inserts into the `voices` table and add image handling:

```python
# After the existing audio copy block, before the INSERT:
image_path = None
for ext in ("png", "jpg", "jpeg"):
    src_image = _repo / char.ref_audio_path.replace(
        Path(char.ref_audio_path).name,
        f"{slug}.{ext}"
    )
    # Simpler: look directly in the _repo directory
    src_image = _repo / f"_voices/{slug}.{ext}"
    if src_image.exists():
        dest_image = voice_dir / f"voice_{slug}_image.{ext}"
        shutil.copy2(src_image, dest_image)
        image_path = str(dest_image)
        break

# Then include image_path in the INSERT statement:
db.execute(
    """INSERT INTO voices
       (name, slug, description, voice_type,
        sample_audio_path, ref_audio_path, ref_text,
        speaking_style, emotional_range, image_path)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (char.name, slug, char.voice_description, "",
     str(sample_dest), str(ref_dest), char.ref_text,
     char.speaking_style, json.dumps(char.emotional_range),
     image_path),
)
```

> **Note:** The existing INSERT in `seed_voices.py` does not include `image_path`. Add it to both the column list and the values tuple. The column already exists in the database schema (`models/db.py`).

### Step 3 — Re-seed (if needed)

Images are only seeded on **first insert** (when the voice is not already in the DB). If voices are already seeded without images, either:

- Use the **web UI** to upload images individually (Option 1 above), or
- Clear the voices from the DB and restart the worker to re-seed:

```bash
# ⚠️ This removes ALL voice records including any you've added manually
sqlite3 data/weirding.db "DELETE FROM voices WHERE voice_type='';"
# Then restart the service — seed_voices runs automatically on startup
launchctl kickstart -k gui/$(id -u)/com.jeremy.weirding.web
```

---

## File naming convention

When images are stored, they follow this convention:

| Field | Value |
|---|---|
| Filename | `voice_{slug}_image.{ext}` |
| Location | `VOICE_DIR/` (default: `<repo>/data/cache/voices/`) |
| DB column | `voices.image_path` (absolute path) |
| Supported formats | PNG, JPG/JPEG |

The image is served via the `/static/voice-images/` static file mount.

---

## Do I need to touch `characters_data.py`?

**No.** `characters_data.py` defines only audio/voice properties:

- `name`, `voice_description`, `emotional_range`, `speaking_style`
- `ref_audio_path`, `ref_text`

There is no `image_path` field in the `Character` dataclass and none is needed — profile images are managed at the database layer, not the character definition layer. This keeps `characters_data.py` framework-agnostic and reusable across different scripts.
