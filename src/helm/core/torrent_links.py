"""Resolve magnet, torrent metadata, and HTML detail links with bounded I/O."""

import base64
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from helm.core.http import get_shared_session

MAX_METADATA = 8 * 1024 * 1024


@dataclass
class TorrentSource:
    magnet: str
    data: bytes = b""


def validate_magnet(value):
    if len(value) > 65536 or urlsplit(value).scheme.lower() != "magnet":
        raise ValueError("Expected a magnet URI")
    topics = parse_qs(urlsplit(value).query).get("xt", [])
    valid = any(
        re.fullmatch(r"urn:btih:(?:[a-fA-F0-9]{40}|[A-Z2-7a-z]{32})", topic)
        or re.fullmatch(r"urn:btmh:1220[a-fA-F0-9]{64}", topic)
        for topic in topics
    )
    if not valid:
        raise ValueError("Magnet URI has no supported info hash")
    return value


def decode_metadata(data):
    """Decode bounded bencode, retaining the exact encoded info dictionary."""
    if not data or len(data) > MAX_METADATA:
        raise ValueError("Torrent metadata is empty or exceeds 8 MiB")
    position = 0
    info_bytes = None

    def read(depth=0):
        nonlocal position, info_bytes
        if depth > 64 or position >= len(data):
            raise ValueError("Invalid torrent metadata nesting")
        marker = data[position : position + 1]
        if marker == b"i":
            end = data.index(b"e", position)
            raw = data[position + 1 : end]
            if not re.fullmatch(rb"0|-?[1-9][0-9]*", raw):
                raise ValueError("Invalid bencode integer")
            position = end + 1
            return int(raw)
        if marker in (b"d", b"l"):
            position += 1
            result = {} if marker == b"d" else []
            while position < len(data) and data[position : position + 1] != b"e":
                if marker == b"d":
                    key = read(depth + 1)
                    if not isinstance(key, bytes) or key in result:
                        raise ValueError("Invalid dictionary key")
                    start = position
                    result[key] = read(depth + 1)
                    if depth == 0 and key == b"info":
                        info_bytes = data[start:position]
                else:
                    result.append(read(depth + 1))
            if position >= len(data):
                raise ValueError("Unterminated bencode container")
            position += 1
            return result
        if marker.isdigit():
            end = data.index(b":", position)
            raw = data[position:end]
            if not re.fullmatch(rb"0|[1-9][0-9]*", raw):
                raise ValueError("Invalid bencode string length")
            length = int(raw)
            position = end + 1
            if position + length > len(data):
                raise ValueError("Truncated bencode string")
            value = data[position : position + length]
            position += length
            return value
        raise ValueError("Invalid bencode marker")

    try:
        root = read()
    except (ValueError, IndexError, OverflowError) as error:
        raise ValueError("Invalid torrent metadata") from error
    if (
        position != len(data)
        or not isinstance(root, dict)
        or not isinstance(root.get(b"info"), dict)
        or info_bytes is None
    ):
        raise ValueError("Torrent metadata has no info dictionary")
    info = root[b"info"]
    if (
        not isinstance(info.get(b"name"), bytes)
        or not isinstance(info.get(b"piece length"), int)
        or info[b"piece length"] <= 0
    ):
        raise ValueError("Invalid torrent info fields")
    if b"pieces" not in info and info.get(b"meta version") != 2:
        raise ValueError("Torrent has no piece hashes")
    if b"pieces" in info:
        pieces = info[b"pieces"]
        if not isinstance(pieces, bytes) or not pieces or len(pieces) % 20:
            raise ValueError("Invalid torrent piece hashes")
        length = info.get(b"length")
        files = info.get(b"files")
        if not (isinstance(length, int) and length >= 0) and not valid_files(files):
            raise ValueError("Invalid torrent file list")
    return root, info_bytes


def valid_files(files):
    if not isinstance(files, list) or not files:
        return False
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get(b"length"), int) or entry[b"length"] < 0:
            return False
        path = entry.get(b"path")
        if not isinstance(path, list) or not path:
            return False
        if not all(isinstance(part, bytes) and part not in (b"", b".", b"..") and b"/" not in part for part in path):
            return False
    return True


def metadata_source(data):
    root, info = decode_metadata(data)
    details = root[b"info"]
    topics = []
    if b"pieces" in details:
        topics.append("xt=urn:btih:" + hashlib.sha1(info).hexdigest())
    if details.get(b"meta version") == 2:
        topics.append("xt=urn:btmh:1220" + hashlib.sha256(info).hexdigest())
    topics.append("dn=" + quote(details[b"name"].decode("utf-8", errors="replace")))
    trackers = []
    if isinstance(root.get(b"announce"), bytes):
        trackers.append(root[b"announce"])
    for tier in root.get(b"announce-list", []):
        if isinstance(tier, list):
            trackers.extend(value for value in tier if isinstance(value, bytes))
    for tracker in dict.fromkeys(trackers):
        topics.append("tr=" + quote(tracker.decode("utf-8", errors="replace"), safe=""))
    return TorrentSource("magnet:?" + "&".join(topics), data)


def resolve_torrent(value, timeout=30, max_hops=6, allow_local=True):
    if isinstance(value, TorrentSource):
        return value
    value = str(value).strip()
    if value.lower().startswith("magnet:"):
        return TorrentSource(validate_magnet(value))
    if allow_local and not urlsplit(value).scheme:
        path = Path(value).expanduser()
        if not path.is_file() or path.suffix.lower() != ".torrent":
            raise ValueError("Expected an existing .torrent file")
        with path.open("rb") as stream:
            return metadata_source(stream.read(MAX_METADATA + 1))
    deadline = time.monotonic() + timeout
    seen = set()
    for _ in range(max_hops):
        if value in seen:
            raise ValueError("Torrent link contains a redirect loop")
        seen.add(value)
        if urlsplit(value).scheme.lower() not in ("http", "https"):
            raise ValueError("Only HTTP(S), magnet URIs and local .torrent files are supported")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Torrent link resolution exceeded its deadline")
        response = get_shared_session().get(value, timeout=min(10, remaining), stream=True, allow_redirects=False)
        try:
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Redirect has no Location")
                value = urljoin(value, location)
                if value.lower().startswith("magnet:"):
                    return TorrentSource(validate_magnet(value))
                continue
            chunks = []
            total = 0
            for chunk in response.iter_content(65536):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Torrent link resolution exceeded its deadline")
                total += len(chunk)
                if total > MAX_METADATA:
                    raise ValueError("Torrent response exceeds 8 MiB")
                chunks.append(chunk)
            data = b"".join(chunks)
            if data.startswith(b"d"):
                try:
                    return metadata_source(data)
                except ValueError:
                    pass  # Plain text or HTML can also begin with "d".
            # Bytes let BeautifulSoup sniff the charset; a server's label may be invalid.
            soup = BeautifulSoup(data, "html.parser")
            links = [urljoin(value, anchor.get("href", "")) for anchor in soup.find_all("a", href=True)]
            magnets = [link for link in links if link.lower().startswith("magnet:")]
            if magnets:
                return TorrentSource(validate_magnet(magnets[0]))
            torrents = [link for link in links if urlsplit(link).path.lower().endswith(".torrent")]
            if not torrents:
                raise ValueError("Detail page contains no magnet or .torrent link")
            value = torrents[0]
        finally:
            response.close()
    raise ValueError("Torrent resolution exceeded its redirect/detail-page limit")


def info_hash(magnet):
    for topic in parse_qs(urlsplit(magnet).query).get("xt", []):
        if topic.lower().startswith("urn:btih:"):
            value = topic.rsplit(":", 1)[1]
            if len(value) == 32:
                return base64.b32decode(value.upper()).hex()
            return value.lower()
        if topic.lower().startswith("urn:btmh:1220"):
            return topic.rsplit(":", 1)[1][4:].lower()
    raise ValueError("No supported torrent hash")
