"""Feeding files to workers: path/glob resolution, secret file filter, chunking.

Purpose: Allow Claude to provide only paths without loading file contents into its own context;
the server reads the content and forwards it to the worker.
"""
import fnmatch, glob, os

HOME = os.path.expanduser("~")

# Build/tool directories skipped in directory scans
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".godot", ".import", ".next",
             ".venv", "venv", "obj", "bin", ".mypy_cache", ".pytest_cache"}

# Files that are never sent out (key, password, credential)
SECRET_NAMES = [".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx",
                "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "*.keystore", "*.jks",
                ".netrc", ".npmrc", ".pypirc", "credentials*", "*secret*", "*.gpg",
                ".git-credentials", "auth.json", "token*.json"]
_SECRET_SUBDIRS = (".ssh", ".gnupg", ".aws", ".kube", ".docker",
                   ".config/ai-workers", ".config/gh", ".password-store")


def _secret_dirs(home):
    """Secret directories under `home`, both as written and symlink-resolved
    (_is_secret compares realpaths, so a symlinked home must not hide them)."""
    out = []
    for d in _SECRET_SUBDIRS:
        p = os.path.join(home, d)
        for form in (p, os.path.realpath(p)):
            if form not in out:
                out.append(form)
    return out


SECRET_DIRS = _secret_dirs(HOME)

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
    """Converts path/glob list to file list.

    Returns: (files, skipped[{path, reason}], error|None)
    """
    if isinstance(patterns, str):
        patterns = [patterns]
    files, skipped, seen = [], [], set()
    for pat in patterns or []:
        p = os.path.expanduser(pat)
        if not os.path.isabs(p):
            return [], skipped, f"absolute path required (or with ~): {pat}"
        if os.path.isdir(p):
            p = os.path.join(p, "**", "*")
        expanded = glob.has_magic(p)  # glob or directory -> skip tool directories
        matches = sorted(glob.glob(p, recursive=True)) if expanded else [p]
        if not matches:
            skipped.append({"path": pat, "reason": "no matching files"})
        for m in matches:
            if m in seen or os.path.isdir(m):
                continue
            seen.add(m)
            if not os.path.exists(m):
                skipped.append({"path": m, "reason": "does not exist"})
            elif expanded and _skipped_dir(m):
                continue  # silently skip node_modules etc.
            elif _is_secret(m):
                skipped.append({"path": m, "reason": "secret file (not sent)"})
            else:
                try:
                    too_big = os.path.getsize(m) > MAX_FILE_BYTES
                except OSError:
                    skipped.append({"path": m, "reason": "unreadable"})
                    continue
                if too_big:
                    skipped.append({"path": m, "reason": f"> {MAX_FILE_BYTES // 1_000_000} MB"})
                else:
                    files.append(m)
            if len(files) > MAX_FILES:
                return [], skipped, f"too many files (> {MAX_FILES}); narrow the glob"
    return files, skipped, None


def read_text(path):
    """Read text file; None if binary. Raises OSError if unreadable."""
    with open(path, "rb") as f:
        raw = f.read()
    if b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def chunk(text, size):
    """Split text into chunks of at most `size` characters at line boundaries."""
    if len(text) <= size:
        return [text]
    parts, cur, cur_len = [], [], 0
    for line in text.splitlines(keepends=True):
        while len(line) > size:  # hard split if a single line exceeds the limit
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
    try:
        root = os.path.commonpath(files)
    except ValueError:  # e.g. different Windows drives
        return ""
    return root if os.path.isdir(root) else os.path.dirname(root)


def label(path, root):
    try:
        rel = os.path.relpath(path, root) if root else path
    except ValueError:
        return path
    return rel if not rel.startswith("..") else path


def bundle(patterns):
    """For delegate: combine all files into a single text.

    Returns: (text, file_count, skipped, error|None)
    """
    files, skipped, err = resolve(patterns)
    if err:
        return None, 0, skipped, err
    root = common_root(files)
    blocks = []
    for fp in files:
        try:
            text = read_text(fp)
        except OSError:
            skipped.append({"path": fp, "reason": "unreadable"})
            continue
        if text is None:
            skipped.append({"path": fp, "reason": "binary file"})
            continue
        blocks.append(f"### FILE: {label(fp, root)}\n{text}")
    return "\n\n".join(blocks), len(blocks), skipped, None


def items(patterns, chunk_chars):
    """For fanout: each file (or chunk if large) is a separate task.

    Returns: (items[{label, input}], skipped, error|None)
    """
    files, skipped, err = resolve(patterns)
    if err:
        return [], skipped, err
    root = common_root(files)
    out = []
    for fp in files:
        try:
            text = read_text(fp)
        except OSError:
            skipped.append({"path": fp, "reason": "unreadable"})
            continue
        if text is None:
            skipped.append({"path": fp, "reason": "binary file"})
            continue
        name = label(fp, root)
        parts = chunk(text, chunk_chars)
        for i, part in enumerate(parts, 1):
            lab = name if len(parts) == 1 else f"{name} [{i}/{len(parts)}]"
            out.append({"label": lab, "input": f"### FILE: {lab}\n{part}"})
    return out, skipped, None


def format_skipped(skipped, limit=15):
    if not skipped:
        return ""
    lines = [f"- {s['path']}: {s['reason']}" for s in skipped[:limit]]
    if len(skipped) > limit:
        lines.append(f"- ... +{len(skipped) - limit} more files")
    return "\n## Skipped files\n" + "\n".join(lines)
