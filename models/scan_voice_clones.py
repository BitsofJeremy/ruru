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

def scan_voice_clones(db_path, clones_dir, voice_dir) -> int:
    if not clones_dir.exists(): return 0
    audio, text = [], []
    for f in clones_dir.iterdir():
        if f.suffix.lower() in {'.mp3','.wav'}: audio.append(f)
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
            slug = re.sub(r'[^a-z0-9]+','-',ns).strip('-')
            if slug in seen: continue
            vs = f"{slug}_clone"
            ad = voice_dir / f"{vs}{af.suffix}"; td = voice_dir / f"{vs}.txt"
            with open(af,'rb') as s, open(ad,'wb') as d: d.write(s.read())
            with open(tf,'r') as s, open(td,'w') as d: d.write(s.read())
            cur.execute("INSERT INTO voices (name,slug,voice_type,ref_audio_path,ref_text,sample_audio_path,speaking_style,emotional_range,description) VALUES (?,?,?,?,?,?,?,?,?)",
                (ns.replace('-',' ').title(), slug, 'cloned', str(ad.resolve()), str(td.resolve()), str(ad.resolve()), '', '[]', ''))
            seen.add(slug); n += 1
        conn.commit()
    finally:
        conn.close()
    return n