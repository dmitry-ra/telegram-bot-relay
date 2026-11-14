"""Bounded naming hints from declarations, never file-content detection."""

from pathlib import PurePosixPath

from pydantic import JsonValue

MEDIA_KINDS = frozenset(
    ("photo", "document", "voice", "audio", "video", "video_note", "animation", "sticker")
)

# The first suffix is the prospective name when only MIME is available.
# Aliases are compatible declarations, not evidence about the actual bytes.
MIME_SUFFIXES: dict[str, tuple[str, ...]] = {
    "image/jpeg": (".jpg", ".jpeg", ".jpe"),
    "image/png": (".png",),
    "image/gif": (".gif",),
    "image/webp": (".webp",),
    "image/heic": (".heic",),
    "image/heif": (".heif",),
    "image/avif": (".avif",),
    "image/bmp": (".bmp",),
    "image/tiff": (".tif", ".tiff"),
    "image/svg+xml": (".svg",),
    "audio/ogg": (".ogg", ".oga", ".opus"),
    "application/ogg": (".ogg", ".oga", ".opus"),
    "audio/mpeg": (".mp3",),
    "audio/mp4": (".m4a",),
    "audio/aac": (".aac",),
    "audio/wav": (".wav",),
    "audio/x-wav": (".wav",),
    "audio/flac": (".flac",),
    "audio/amr": (".amr",),
    "video/mp4": (".mp4", ".m4v"),
    "video/quicktime": (".mov",),
    "video/webm": (".webm",),
    "video/x-matroska": (".mkv",),
    "application/pdf": (".pdf",),
    "application/zip": (".zip",),
    "application/gzip": (".gz", ".tgz"),
    "application/x-tgsticker": (".tgs",),
    "application/x-7z-compressed": (".7z",),
    "application/x-rar-compressed": (".rar",),
    "application/json": (".json",),
    "application/xml": (".xml",),
    "text/plain": (".txt", ".log", ".md", ".csv", ".tsv"),
    "text/markdown": (".md",),
    "text/csv": (".csv",),
    "text/html": (".html", ".htm"),
    "text/xml": (".xml",),
    "application/msword": (".doc",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (".docx",),
    "application/vnd.ms-excel": (".xls",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (".xlsx",),
    "application/vnd.ms-powerpoint": (".ppt",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (".pptx",),
}
SAFE_SUFFIXES = frozenset(suffix for choices in MIME_SUFFIXES.values() for suffix in choices) | {
    ".tar",
    ".bz2",
    ".xz",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".conf",
    ".py",
    ".js",
    ".ts",
    ".sh",
    ".go",
    ".rs",
    ".cs",
    ".sql",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
    ".aif",
    ".aiff",
    ".avi",
    ".ico",
}


def claim(value: JsonValue, maximum: int = 1024) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value:
        return value
    return None


def suffix(value: str | None) -> str | None:
    if value is None or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    # Original names may use either platform's separators. A basename is only
    # examined; none of it ever becomes a local path or local basename.
    extension = PurePosixPath(value.replace("\\", "/")).suffix.lower()
    return extension if extension in SAFE_SUFFIXES else None


def compatible(first: str, second: str) -> bool:
    return first == second or any(
        first in choices and second in choices for choices in MIME_SUFFIXES.values()
    )


def telegram_path(value: JsonValue) -> str | None:
    path = claim(value)
    # getFile supplies a path, never a token-bearing download URL. Local Bot
    # API servers may supply absolute paths; these remain advisory metadata.
    if (
        path
        and not any(token in path for token in ("://", "?", "#"))
        and not any(ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        return path
    return None


def hints(
    descriptor: dict[str, JsonValue],
    kinds: tuple[str, ...],
    telegram: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {"media_kinds": list(kinds)}
    name = claim(descriptor.get("file_name"))
    mime = claim(descriptor.get("mime_type"), 256)
    api_path = telegram_path(telegram.get("file_path")) if telegram is not None else None
    for key, value in (("file_name", name), ("mime_type", mime), ("telegram_file_path", api_path)):
        if value is not None:
            result[key] = value
    if telegram is not None and type(telegram.get("file_size")) is int:
        result["telegram_file_size"] = telegram["file_size"]
    mime_suffixes = MIME_SUFFIXES.get(mime.partition(";")[0].strip().lower(), ()) if mime else ()
    named, remote = suffix(name), suffix(api_path)
    kind_suffix = None
    # Static stickers, generic audio/video/voice and animations can have more
    # than one format. Only the two unambiguous sticker flags supply a fallback.
    if set(kinds) == {"sticker"}:
        if descriptor.get("is_animated") is True and descriptor.get("is_video") is not True:
            kind_suffix = ".tgs"
        elif descriptor.get("is_video") is True and descriptor.get("is_animated") is not True:
            kind_suffix = ".webm"
    choices = (
        (named, "file_name"),
        (mime_suffixes[0] if mime_suffixes else None, "mime_type"),
        (remote, "telegram_file_path"),
        (kind_suffix, "media_kind"),
    )
    selected, source = next(
        ((value, origin) for value, origin in choices if value), (".bin", "unknown")
    )
    result.update(suffix=selected, naming_source=source)
    conflicts: list[JsonValue] = []
    if named and mime_suffixes and named not in mime_suffixes:
        conflicts.append("file_name_mime_type")
    if named and remote and not compatible(named, remote):
        conflicts.append("file_name_telegram_file_path")
    if remote and mime_suffixes and remote not in mime_suffixes:
        conflicts.append("mime_type_telegram_file_path")
    if conflicts:
        result["conflicts"] = conflicts
    return result
