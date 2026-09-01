#!/usr/bin/env python3
"""Verify the static files required for an IQ Dominoes Pages deployment.

This intentionally uses only the Python standard library so it can run in a
clean CI checkout before publishing the site.
"""

from __future__ import annotations

import argparse
import json
import stat
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


ANDROID_PACKAGE = "com.iqdominoes.app"
ANDROID_FINGERPRINT = "E8:14:3F:65:31:E2:A0:1F:A7:9B:FC:4F:DA:76:4C:20:E1:D0:E9:0A:8D:94:DF:71:B0:93:66:A8:6E:75:7D:DC"
APPLE_APP_ID = "2CHGB59PS9.com.iqdominoes.app"
JOIN_PATH = "/join/*"
EXPECTED_CNAME = "www.iqdominoes.com"
SITE_ORIGIN = "https://deployment.invalid"
CRITICAL_FILES = (
    ".nojekyll",
    "CNAME",
    ".well-known/assetlinks.json",
    ".well-known/apple-app-site-association",
)
REQUIRED_ROUTE_INDEXES = (
    "join/index.html",
    "partners/index.html",
    "privacy/index.html",
    "terms/index.html",
    "es/privacy/index.html",
    "es/terms/index.html",
    "torneo/index.html",
    "torneo/registro/index.html",
)

PARTNERS_RUNTIME_ENDPOINT = "https://api.iqdominoes.com/api/public/partners"
TOURNAMENT_UNAVAILABLE_MARKER = "This page is not available."
TOURNAMENT_FORBIDDEN_SURFACE_MARKERS = (
    "<form",
    "/api/torneo",
    "countdown",
    "register now",
    "spectator",
)


def display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def is_regular_file(root: Path, path: Path) -> bool:
    """True only for a non-symlink regular file below the lexical site root."""
    try:
        relative = path.relative_to(root)
        component = root
        for part in relative.parts:
            component /= part
            if stat.S_ISLNK(component.lstat().st_mode):
                return False
        return stat.S_ISREG(path.lstat().st_mode)
    except (OSError, ValueError):
        return False


def load_json(path: Path, root: Path, errors: list[str]):
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{display_path(root, path)}: invalid JSON: {exc}")
        return None


def decode_url_path(value: str) -> str:
    """Decode repeatedly to catch encoded traversal such as %252e%252e/."""
    decoded = value
    for _ in range(4):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def local_target(root: Path, source: Path, value: str):
    """Return (target, reason), or (None, None) for references to ignore."""
    value = value.strip()
    if not value or value.startswith("#"):
        return None, None
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        return None, f"is not a valid URL: {exc}"
    if parts.scheme or parts.netloc or value.startswith("//"):
        return None, None

    path_part = decode_url_path(parts.path)
    if not path_part:
        return None, None
    # URL paths use '/', but reject backslashes too so a checkout on another
    # platform cannot turn an apparently-local target into a traversal.
    if "\\" in path_part or "\x00" in path_part:
        return None, "contains an unsafe path character"
    if path_part.startswith("/"):
        candidate = root / path_part.lstrip("/")
    else:
        candidate = source.parent / path_part
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None, "escapes the site root after percent-decoding"
    return resolved, None


def target_exists(root: Path, target: Path) -> bool:
    """Accept direct files, or route directories with a safe index document."""
    return target.is_file() or (
        target.is_dir() and is_regular_file(root, target / "index.html")
    )


class ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[int, str, str]] = []
        self.base_href: tuple[int, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line, _ = self.getpos()
        for name, value in attrs:
            if tag.lower() == "base" and name.lower() == "href":
                if self.base_href is None and value is not None:
                    self.base_href = (line, value)
                continue
            if name.lower() in {"href", "src"} and value is not None:
                self.references.append((line, name.lower(), value))


def document_url(root: Path, html_path: Path) -> str:
    return f"{SITE_ORIGIN}/{html_path.relative_to(root).as_posix()}"


def base_url(root: Path, html_path: Path, parser: ReferenceParser, errors: list[str]) -> str:
    """Resolve the first HTML base element, retaining browser external behavior."""
    document = document_url(root, html_path)
    if parser.base_href is None:
        return document
    line, value = parser.base_href
    value = value.strip()
    if not value:
        return document
    target, reason = local_target(root, html_path, value)
    if reason:
        errors.append(f"{display_path(root, html_path)}:{line}: base href={value!r} {reason}")
        return document
    # An external base makes relative references external browser targets.  Do
    # not try to verify resources the deployed site does not serve itself.
    return urljoin(document, value)


def html_reference_target(root: Path, effective_base: str, value: str):
    """Resolve an HTML reference as a browser would, then map local URLs to disk."""
    value = value.strip()
    if not value or value.startswith("#"):
        return None, None
    try:
        resolved_url = urljoin(effective_base, value)
        parts = urlsplit(resolved_url)
    except ValueError as exc:
        return None, f"is not a valid URL: {exc}"
    if parts.scheme != "https" or parts.netloc != "deployment.invalid":
        return None, None
    return local_target(root, root / parts.path.lstrip("/"), parts.path)


def verify_html(root: Path, errors: list[str]) -> None:
    for html_path in sorted(root.rglob("*.html")):
        parser = ReferenceParser()
        relative = display_path(root, html_path)
        if not is_regular_file(root, html_path):
            errors.append(f"{relative}: HTML document is a symlink or not a regular file")
            continue
        try:
            parser.feed(html_path.read_text(encoding="utf-8"))
            parser.close()
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{relative}: cannot parse HTML: {exc}")
            continue
        effective_base = base_url(root, html_path, parser, errors)
        for line, attribute, value in parser.references:
            target, reason = html_reference_target(root, effective_base, value)
            if reason:
                errors.append(f"{relative}:{line}: {attribute}={value!r} {reason}")
            elif target is not None and not target_exists(root, target):
                errors.append(
                    f"{relative}:{line}: {attribute}={value!r} does not resolve "
                    "to a file or directory index"
                )


def verify_cname(root: Path, errors: list[str]) -> None:
    path = root / "CNAME"
    if not is_regular_file(root, path):
        return
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"CNAME: cannot read file: {exc}")
        return
    if [line.strip() for line in content.splitlines()] != [EXPECTED_CNAME]:
        errors.append(f"CNAME: expected one hostname line containing {EXPECTED_CNAME!r}")


def verify_required_routes(root: Path, errors: list[str]) -> None:
    for route_index in REQUIRED_ROUTE_INDEXES:
        if not is_regular_file(root, root / route_index):
            errors.append(
                f"{route_index}: required compatibility route is missing or is a symlink"
            )


def verify_product_surfaces(root: Path, errors: list[str]) -> None:
    partners_path = root / "partners/index.html"
    if is_regular_file(root, partners_path):
        try:
            partners_html = partners_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"partners/index.html: cannot read file: {exc}")
        else:
            if PARTNERS_RUNTIME_ENDPOINT not in partners_html:
                errors.append(
                    "partners/index.html: must fetch the sponsor-safe runtime contract"
                )

    for relative in ("torneo/index.html", "torneo/registro/index.html"):
        path = root / relative
        if not is_regular_file(root, path):
            continue
        try:
            html = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{relative}: cannot read file: {exc}")
            continue
        normalized = html.lower()
        if TOURNAMENT_UNAVAILABLE_MARKER.lower() not in normalized:
            errors.append(f"{relative}: dormant compatibility page marker is missing")
        for marker in TOURNAMENT_FORBIDDEN_SURFACE_MARKERS:
            if marker in normalized:
                errors.append(
                    f"{relative}: dormant compatibility page exposes forbidden marker {marker!r}"
                )


def verify_associations(root: Path, errors: list[str]) -> None:
    assetlinks_path = root / ".well-known/assetlinks.json"
    if is_regular_file(root, assetlinks_path):
        assetlinks = load_json(assetlinks_path, root, errors)
        expected_assetlinks = [{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": ANDROID_PACKAGE,
                "sha256_cert_fingerprints": [ANDROID_FINGERPRINT],
            },
        }]
        if assetlinks is not None and assetlinks != expected_assetlinks:
            errors.append(
                ".well-known/assetlinks.json: must exactly contain the intended "
                f"Android association for {ANDROID_PACKAGE!r} and its pinned fingerprint"
            )

    apple_path = root / ".well-known/apple-app-site-association"
    if is_regular_file(root, apple_path):
        aasa = load_json(apple_path, root, errors)
        expected_aasa = {"applinks": {"apps": [], "details": [{
            "appID": APPLE_APP_ID, "paths": [JOIN_PATH],
        }]}}
        if aasa is not None and aasa != expected_aasa:
            errors.append(
                ".well-known/apple-app-site-association: must exactly contain the intended "
                f"Apple association for {APPLE_APP_ID!r} and path {JOIN_PATH!r}"
            )


def verify_manifest(root: Path, errors: list[str]) -> None:
    path = root / "manifest.webmanifest"
    if not is_regular_file(root, path):
        errors.append("manifest.webmanifest: required manifest is missing or not a regular file")
        return
    manifest = load_json(path, root, errors)
    if not isinstance(manifest, dict):
        if manifest is not None:
            errors.append("manifest.webmanifest: expected a JSON object")
        return
    for field in ("start_url", "scope"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"manifest.webmanifest: {field} must be a nonempty local URL string")
            continue
        target, reason = local_target(root, path, value)
        if reason:
            errors.append(f"manifest.webmanifest: {field}={value!r} {reason}")
        elif target is None:
            errors.append(f"manifest.webmanifest: {field} must target a local site path")
        elif not target_exists(root, target):
            errors.append(f"manifest.webmanifest: {field}={value!r} does not resolve to a file or directory index")
    icons = manifest.get("icons")
    if not isinstance(icons, list) or not icons:
        errors.append("manifest.webmanifest: icons must be a nonempty list")
        return
    for index, icon in enumerate(icons):
        prefix = f"manifest.webmanifest: icons[{index}]"
        if not isinstance(icon, dict) or not isinstance(icon.get("src"), str) or not icon["src"]:
            errors.append(f"{prefix}.src must be a nonempty local URL string")
            continue
        target, reason = local_target(root, path, icon["src"])
        if reason:
            errors.append(f"{prefix}.src={icon['src']!r} {reason}")
        elif target is None:
            errors.append(f"{prefix}.src must target a local site path")
        elif not target_exists(root, target):
            errors.append(f"{prefix}.src={icon['src']!r} does not resolve to a file or directory index")


def verify_roster(root: Path, errors: list[str]) -> None:
    path = root / "torneo/roster.json"
    if not is_regular_file(root, path):
        errors.append("torneo/roster.json: required roster is missing or not a regular file")
        return
    roster = load_json(path, root, errors)
    if not isinstance(roster, dict):
        if roster is not None:
            errors.append("torneo/roster.json: expected a JSON object")
        return
    if not isinstance(roster.get("updatedAt"), str):
        errors.append("torneo/roster.json: updatedAt must be a string")
    slots = roster.get("slots")
    if not isinstance(slots, list):
        errors.append("torneo/roster.json: slots must be a list")
        return
    seen: set[int] = set()
    for index, slot in enumerate(slots):
        prefix = f"torneo/roster.json: slots[{index}]"
        if not isinstance(slot, dict):
            errors.append(f"{prefix} must be an object with n and handle")
            continue
        number = slot.get("n")
        handle = slot.get("handle")
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 16:
            errors.append(f"{prefix}.n must be an integer from 1 through 16")
        elif number in seen:
            errors.append(f"{prefix}.n duplicates slot number {number}")
        else:
            seen.add(number)
        if not isinstance(handle, str) or not handle.strip():
            errors.append(f"{prefix}.handle must be a nonempty string")


def verify_deployment(root: Path) -> list[str]:
    """Return every deployment problem found below *root*."""
    root = root.resolve()
    errors: list[str] = []
    for name in CRITICAL_FILES:
        path = root / name
        if not is_regular_file(root, path):
            errors.append(f"{name}: required regular file is missing or is a symlink")
    verify_cname(root, errors)
    verify_associations(root, errors)
    verify_required_routes(root, errors)
    verify_product_surfaces(root, errors)
    verify_manifest(root, errors)
    verify_roster(root, errors)
    verify_html(root, errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="site root to validate")
    args = parser.parse_args(argv)
    errors = verify_deployment(args.root)
    if errors:
        print("Deployment verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Deployment verification passed: {args.root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
