from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from base64 import b64encode
from pathlib import Path

from app.core.utils_text import normalize_text


_SIGNATURE_HTML_PATHS: dict[str, dict[str, str]] = {}


def _clean_signature_map(raw: object) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, dict[str, str]] = {}
    for user, profiles in raw.items():
        if not isinstance(profiles, dict):
            continue
        user_key = normalize_text(str(user))
        if not user_key:
            continue
        clean[user_key] = {
            str(profile_key): str(profile_path)
            for profile_key, profile_path in profiles.items()
        }
    return clean


def default_signature_paths() -> dict[str, dict[str, str]]:
    return {user: dict(profiles) for user, profiles in _SIGNATURE_HTML_PATHS.items()}


def signature_paths_for_config(config: object) -> dict[str, dict[str, str]]:
    managed = bool(getattr(config, "email_signatures_managed", False))
    configured = _clean_signature_map(getattr(config, "email_signatures", {}))
    if managed:
        return configured

    merged = default_signature_paths()
    for user, profiles in configured.items():
        if user in merged:
            merged[user].update(profiles)
        else:
            merged[user] = dict(profiles)
    return merged


def signature_owner_options(config: object) -> list[str]:
    names = [name for name in signature_paths_for_config(config).keys() if str(name).strip()]
    return sorted(names, key=lambda value: normalize_text(value))


def first_signature_owner(config: object) -> str:
    options = signature_owner_options(config)
    return str(options[0]).title() if options else ""

_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", flags=re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(r"<meta\b[^>]*>", flags=re.IGNORECASE)
_LINK_RE = re.compile(r"<link\b[^>]*>", flags=re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", flags=re.IGNORECASE | re.DOTALL)
_A_HREF_RE = re.compile(r'(<a\b[^>]*\bhref\s*=\s*["\'])([^"\']+)(["\'])', flags=re.IGNORECASE)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE)
_IMG_STYLE_RE = re.compile(r'\bstyle\s*=\s*["\']([^"\']*)["\']', flags=re.IGNORECASE)
_IMG_SRC_RE = re.compile(r'\bsrc\s*=\s*["\']([^"\']+)["\']', flags=re.IGNORECASE)
_REMOTE_ASSET_VERSION_CACHE: dict[str, str] = {}
_NAS_HOST_PREFIX = ""
_NAS_IP_PREFIX = ""


def _normalize_anchor_href(fragment: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        prefix, href, suffix = match.groups()
        value = (href or "").strip()
        if not value:
            return match.group(0)
        lower = value.lower()
        if lower.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
            return match.group(0)
        return f"{prefix}https://{value}{suffix}"

    return _A_HREF_RE.sub(_repl, fragment)


def _normalize_img_tag(match: re.Match[str]) -> str:
    tag = match.group(0)
    src_match = _IMG_SRC_RE.search(tag)
    if src_match:
        src = (src_match.group(1) or "").strip()
        if src and src.lower().startswith("http://"):
            tag = tag.replace(src, "https://" + src[7:], 1)

    style_match = _IMG_STYLE_RE.search(tag)
    base_style = "max-width:100%;height:auto;display:block;"
    if style_match:
        current = (style_match.group(1) or "").strip()
        merged = f"{current};{base_style}" if current else base_style
        tag = tag[:style_match.start(1)] + merged + tag[style_match.end(1):]
    elif tag.endswith("/>"):
        tag = tag[:-2] + f' style="{base_style}" />'
    else:
        tag = tag[:-1] + f' style="{base_style}">'
    return tag


def sanitize_signature_html(signature_html: str) -> str:
    sig_raw = str(signature_html or "").strip()
    if not sig_raw:
        return ""

    match = _BODY_RE.search(sig_raw)
    fragment = (match.group(1) if match else sig_raw).strip()
    if not fragment:
        return ""

    fragment = _SCRIPT_RE.sub("", fragment)
    fragment = _META_RE.sub("", fragment)
    fragment = _LINK_RE.sub("", fragment)
    fragment = _normalize_anchor_href(fragment)
    fragment = _IMG_TAG_RE.sub(_normalize_img_tag, fragment)
    return fragment.strip()


def _download_binary(url: str, timeout_sec: int = 8) -> tuple[bytes, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ComprasVesper/1.0",
            "Accept": "image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        payload = resp.read()
        mime = ""
        try:
            mime = str(resp.headers.get_content_type() or "").strip().lower()
        except Exception:
            mime = ""
    return payload, mime


def _remote_asset_version(url: str, timeout_sec: int = 3) -> str:
    cached = _REMOTE_ASSET_VERSION_CACHE.get(url)
    if cached is not None:
        return cached

    version = ""
    try:
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={
                "User-Agent": "ComprasVesper/1.0",
                "Accept": "image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            etag = str(resp.headers.get("ETag") or "").strip()
            modified = str(resp.headers.get("Last-Modified") or "").strip()
            raw = etag or modified
            if raw:
                version = re.sub(r"[^A-Za-z0-9]+", "", raw)[:40]
    except Exception:
        version = ""

    _REMOTE_ASSET_VERSION_CACHE[url] = version
    return version


def _with_cache_buster(url: str, version: str) -> str:
    raw_url = str(url or "").strip()
    raw_version = str(version or "").strip()
    if not raw_url or not raw_version:
        return raw_url

    try:
        parsed = urllib.parse.urlsplit(raw_url)
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [(key, value) for key, value in params if key.lower() != "v"]
        filtered.append(("v", raw_version))
        query = urllib.parse.urlencode(filtered)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    except Exception:
        separator = "&" if "?" in raw_url else "?"
        return f"{raw_url}{separator}v={urllib.parse.quote(raw_version)}"


def cache_bust_remote_images(signature_html: str, *, timeout_sec: int = 3) -> str:
    fragment = str(signature_html or "")
    if not fragment:
        return ""

    def _replace_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = _IMG_SRC_RE.search(tag)
        if not src_match:
            return tag

        src = (src_match.group(1) or "").strip()
        if not src or src.lower().startswith("data:"):
            return tag
        if not src.lower().startswith(("http://", "https://")):
            return tag

        version = _remote_asset_version(src, timeout_sec=timeout_sec)
        if not version:
            return tag

        busted_src = _with_cache_buster(src, version)
        if busted_src == src:
            return tag
        return tag[:src_match.start(1)] + busted_src + tag[src_match.end(1):]

    return _IMG_TAG_RE.sub(_replace_tag, fragment)


def inline_remote_images_for_preview(
    signature_html: str,
    *,
    timeout_sec: int = 8,
    max_images: int = 8,
    max_image_bytes: int = 4 * 1024 * 1024,
) -> str:
    fragment = cache_bust_remote_images(sanitize_signature_html(signature_html))
    if not fragment:
        return ""

    cache: dict[str, str] = {}
    processed = 0

    def _replace_tag(match: re.Match[str]) -> str:
        nonlocal processed
        tag = match.group(0)
        if processed >= max_images:
            return tag
        src_match = _IMG_SRC_RE.search(tag)
        if not src_match:
            return tag

        src = (src_match.group(1) or "").strip()
        if not src or src.lower().startswith("data:"):
            return tag
        if not src.lower().startswith(("http://", "https://")):
            return tag

        if src in cache:
            return tag.replace(src, cache[src], 1)

        try:
            data, mime = _download_binary(src, timeout_sec=timeout_sec)
            if not data:
                return tag
            # Protect preview memory usage.
            if max_image_bytes > 0 and len(data) > max_image_bytes:
                return tag
            mime_norm = mime if mime.startswith("image/") else "image/jpeg"
            data_uri = f"data:{mime_norm};base64,{b64encode(data).decode('ascii')}"
            cache[src] = data_uri
            processed += 1
            return tag.replace(src, data_uri, 1)
        except Exception:
            return tag

    return _IMG_TAG_RE.sub(_replace_tag, fragment)


def resolve_signature_html_path(owner: str, profile_key: str, profile_label: str) -> str:
    owner_norm = normalize_text(owner)
    owner_map = {}
    config_managed = False

    try:
        from app.core.config import AppConfig
        cfg = AppConfig.load()
        config_managed = bool(getattr(cfg, "email_signatures_managed", False))
        owner_map = signature_paths_for_config(cfg).get(owner_norm, {})
    except Exception:
        pass

    if not owner_map and not config_managed:
        owner_map = _SIGNATURE_HTML_PATHS.get(owner_norm, {})

    if not owner_map:
        return ""

    key_norm = normalize_text(profile_key)
    if key_norm in owner_map:
        return owner_map[key_norm]

    label_norm = normalize_text(profile_label)
    if label_norm in owner_map:
        return owner_map[label_norm]

    if "vesper" in label_norm:
        return owner_map.get("vesper", "")
    if "ventrio" in label_norm:
        return owner_map.get("ventrio", "")

    return ""


def load_signature_html(path: str) -> str:
    raw_path = str(path or "").strip()
    if not raw_path:
        return ""

    candidates = [raw_path]
    raw_lower = raw_path.lower()
    if raw_lower.startswith(_NAS_HOST_PREFIX):
        candidates.append(_NAS_IP_PREFIX + raw_path[len(_NAS_HOST_PREFIX):])

    for candidate in candidates:
        p = Path(candidate)
        try:
            if not p.exists():
                continue
        except OSError:
            continue
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return p.read_text(encoding=encoding)
            except Exception:
                continue
    return ""


def build_html_email_body(body_text: str, signature_html: str) -> str:
    plain = str(body_text or "").strip()
    if not plain and not signature_html:
        return ""

    text_html = html.escape(plain).replace("\n", "<br>")
    text_block = f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:12pt;">{text_html}</div>'

    sig_fragment = cache_bust_remote_images(sanitize_signature_html(signature_html))
    if not sig_fragment:
        return f"<html><body>{text_block}</body></html>"

    return f"<html><body>{text_block}<br><br>{sig_fragment}</body></html>"
