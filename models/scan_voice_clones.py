import re, sqlite3
from pathlib import Path

def _normalize_stem(filename):
    stem = Path(filename).stem.lower()
    for suffix in ['voice2','voice','sample','ref','text']:
        if stem.endswith('_'+suffix):
            stem = stem[:-(len(suffix)+1)]
    return stem

def _pair_files(audio_files, text_files):
    a = {f: _normalize_stem(f.name) for f in audio_files}
    t = {f: _normalize_stem(f.name) for f in text_files}
    pairs = []
    for af, as_ in a.items():
        for tf, ts in t.items():
            if as_ == ts:
                pairs.append((af, tf)); break
    return pairs

def _parse_prompt(prompt_path):
    """Parse a .prompt.txt into (display_name, persona_title, paragraph).

    Format: line 1 is the display name; the body is a single paragraph
    beginning 'Persona Title - description...'. Returns None for empty,
    unreadable, or undecodable files. persona_title is '' when no ' - '
    separator exists.
    """
    try:
        text = prompt_path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    name = lines[0]
    body = ' '.join(lines[1:])
    if ' - ' in body:
        title, paragraph = body.split(' - ', 1)
        return name, title.strip(), paragraph.strip()
    return name, '', body

def scan_voice_clones(db_path, clones_dir, voice_dir) -> int:
    if not clones_dir.exists(): return 0
    audio, text, prompts = [], [], {}
    for f in clones_dir.iterdir():
        if f.suffix.lower() in {'.mp3','.wav'}: audio.append(f)
        elif f.name.lower().endswith('.prompt.txt'):
            prompts[_normalize_stem(f.name[:-len('.prompt.txt')])] = f
        elif f.suffix.lower() == '.txt': text.append(f)
    if not audio: return 0
    pairs = _pair_files(audio, text)
    if not pairs: return 0
    conn = sqlite3.connect(db_path); cur = conn.cursor()
    n = 0
    try:
        cur.execute("SELECT slug FROM voices")
        seen = {r[0] for r in cur.fetchall()}
        for af, tf in pairs:
            ns = _normalize_stem(af.name)
            slug = re.sub('[^a-z0-9]+','-',ns).strip('-')
            if slug in seen:
                # Backfill legacy imports that predate .prompt.txt support.
                row = cur.execute(
                    "SELECT id, description FROM voices WHERE slug=?", (slug,)
                ).fetchone()
                if row and not row[1] and ns in prompts:
                    parsed = _parse_prompt(prompts[ns])
                    if parsed:
                        p_name, p_title, p_para = parsed
                        p_desc = f"{p_title} — {p_para}" if p_title else p_para
                        cur.execute(
                            "UPDATE voices SET name=?, description=?, speaking_style=? WHERE id=?",
                            (p_name, p_desc, p_title, row[0]))
                continue
            parsed = _parse_prompt(prompts[ns]) if ns in prompts else None
            if parsed:
                name, title, para = parsed
                description = f"{title} — {para}" if title else para
            else:
                name, title, description = ns.replace('-',' ').title(), '', ''
            vs = f"{slug}_clone"
            ad = voice_dir / f"{vs}{af.suffix}"; td = voice_dir / f"{vs}.txt"
            with open(af,'rb') as s, open(ad,'wb') as d: d.write(s.read())
            with open(tf,'r') as s, open(td,'w') as d: d.write(s.read())
            cur.execute("INSERT INTO voices (name,slug,voice_type,ref_audio_path,ref_text,sample_audio_path,speaking_style,emotional_range,description) VALUES (?,?,?,?,?,?,?,?,?)",
                (name, slug, 'cloned', str(ad.resolve()), str(td.resolve()), str(ad.resolve()), title, '[]', description))
            seen.add(slug); n += 1
        conn.commit()
    finally:
        conn.close()
    return n
