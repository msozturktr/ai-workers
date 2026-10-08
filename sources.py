"""Iscilere dosya besleme: yol/glob cozumleme, gizli dosya filtresi, parcalama.

Amac: Claude dosya icerigini kendi context'ine yuklemeden sadece yol versin;
icerigi sunucu okuyup isciye iletsin.
"""
import fnmatch, glob, os

HOME = os.path.expanduser("~")

# Dizin taramalarinda atlanan uretim/arac klasorleri
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".godot", ".import", ".next",
             ".venv", "venv", "obj", "bin", ".mypy_cache", ".pytest_cache"}

# Asla disari gonderilmeyen dosyalar (anahtar, parola, kimlik bilgisi)
SECRET_NAMES = [".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx",
                "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "*.keystore", "*.jks",
                ".netrc", ".npmrc", ".pypirc", "credentials*", "*secret*", "*.gpg",
                ".git-credentials", "auth.json", "token*.json"]
SECRET_DIRS = [os.path.join(HOME, d) for d in
               (".ssh", ".gnupg", ".aws", ".kube", ".docker",
                ".config/ai-workers", ".config/gh", ".password-store")]

MAX_FILES = 300
MAX_FILE_BYTES = 8_000_000


def _is_secret(path):
    real = os.path.realpath(path)
    if any(real == d or real.startswith(d + os.sep) for d in SECRET_DIRS):
        return True
    name = os.path.basename(real).lower()
    return any(fnmatch.fnmatch(name, pat) for pat in SECRET_NAMES)


def _skipped_dir(path):
    return any(part in SKIP_DIRS for part in path.split(os.sep))


def resolve(patterns):
    """Yol/glob listesini dosya listesine cevirir.

    Doner: (dosyalar, atlananlar[{path, reason}], hata|None)
    """
    if isinstance(patterns, str):
        patterns = [patterns]
    files, skipped, seen = [], [], set()
    for pat in patterns or []:
        p = os.path.expanduser(pat)
        if not os.path.isabs(p):
            return [], skipped, f"mutlak yol gerekli (veya ~ ile): {pat}"
        if os.path.isdir(p):
            p = os.path.join(p, "**", "*")
        expanded = glob.has_magic(p)  # glob ya da dizin -> arac klasorlerini atla
        matches = sorted(glob.glob(p, recursive=True)) if expanded else [p]
        if not matches:
            skipped.append({"path": pat, "reason": "eslesen dosya yok"})
        for m in matches:
            if m in seen or os.path.isdir(m):
                continue
            seen.add(m)
            if not os.path.exists(m):
                skipped.append({"path": m, "reason": "yok"})
            elif expanded and _skipped_dir(m):
                continue  # node_modules vb. sessizce atla
            elif _is_secret(m):
                skipped.append({"path": m, "reason": "gizli dosya (gonderilmez)"})
            elif os.path.getsize(m) > MAX_FILE_BYTES:
                skipped.append({"path": m, "reason": f"> {MAX_FILE_BYTES // 1_000_000} MB"})
            else:
                files.append(m)
            if len(files) > MAX_FILES:
                return [], skipped, f"cok fazla dosya (> {MAX_FILES}); glob'u daralt"
    return files, skipped, None


def read_text(path):
    """Metin dosyasini oku; ikili ise None."""
    with open(path, "rb") as f:
        raw = f.read()
    if b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def chunk(text, size):
    """Metni satir sinirlarinda en fazla `size` karakterlik parcalara bol."""
    if len(text) <= size:
        return [text]
    parts, cur, cur_len = [], [], 0
    for line in text.splitlines(keepends=True):
        while len(line) > size:  # tek satir siniri asiyorsa sert kes
            if cur:
                parts.append("".join(cur)); cur, cur_len = [], 0
            parts.append(line[:size]); line = line[size:]
        if cur_len + len(line) > size and cur:
            parts.append("".join(cur)); cur, cur_len = [], 0
        cur.append(line); cur_len += len(line)
    if cur:
        parts.append("".join(cur))
    return parts


def common_root(files):
    if not files:
        return ""
    root = os.path.commonpath(files)
    return root if os.path.isdir(root) else os.path.dirname(root)


def label(path, root):
    rel = os.path.relpath(path, root) if root else path
    return rel if not rel.startswith("..") else path


def bundle(patterns):
    """delegate icin: tum dosyalari tek metinde birlestir.

    Doner: (metin, dosya_sayisi, atlananlar, hata|None)
    """
    files, skipped, err = resolve(patterns)
    if err:
        return None, 0, skipped, err
    root = common_root(files)
    blocks = []
    for fp in files:
        text = read_text(fp)
        if text is None:
            skipped.append({"path": fp, "reason": "ikili dosya"})
            continue
        blocks.append(f"### DOSYA: {label(fp, root)}\n{text}")
    return "\n\n".join(blocks), len(blocks), skipped, None


def items(patterns, chunk_chars):
    """fanout icin: her dosya (buyukse her parca) ayri is.

    Doner: (ogeler[{label, input}], atlananlar, hata|None)
    """
    files, skipped, err = resolve(patterns)
    if err:
        return [], skipped, err
    root = common_root(files)
    out = []
    for fp in files:
        text = read_text(fp)
        if text is None:
            skipped.append({"path": fp, "reason": "ikili dosya"})
            continue
        name = label(fp, root)
        parts = chunk(text, chunk_chars)
        for i, part in enumerate(parts, 1):
            lab = name if len(parts) == 1 else f"{name} [{i}/{len(parts)}]"
            out.append({"label": lab, "input": f"### DOSYA: {lab}\n{part}"})
    return out, skipped, None


def format_skipped(skipped, limit=15):
    if not skipped:
        return ""
    lines = [f"- {s['path']}: {s['reason']}" for s in skipped[:limit]]
    if len(skipped) > limit:
        lines.append(f"- ... +{len(skipped) - limit} dosya daha")
    return "\n## Atlanan dosyalar\n" + "\n".join(lines)
