# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Service-backed MinerU PDF extraction."""
from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.parse import quote

import httpx


_MAX_ARCHIVE_FILES = 10_000
_MAX_ARCHIVE_DOWNLOAD_BYTES = 1024 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024


def _single_file(root: Path, name: str, *, required: bool = False) -> Path | None:
    matches = [path for path in root.rglob(name) if path.is_file()]
    if not matches and required:
        raise RuntimeError(f"MinerU completed but expected output not found: {name}")
    if len(matches) > 1:
        raise RuntimeError(f"MinerU returned multiple files named {name}")
    return matches[0] if matches else None


def _read_outputs(root: Path, stem: str) -> tuple[str, list[dict]]:
    md_path = _single_file(root, "markdown.md", required=True)
    assert md_path is not None
    try:
        markdown_text = md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"MinerU returned non-UTF-8 markdown: {md_path.name}") from exc

    cl_path = _single_file(root, "structured_content.json", required=True)
    assert cl_path is not None
    try:
        parsed = json.loads(cl_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("MinerU returned invalid structured content") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("pages"), list):
        raise RuntimeError("MinerU structured content must contain pages")
    content_list: list[dict] = []
    extensions = parsed.get("extensions", {})
    layout = extensions.get("docvortex_layout", {}) if isinstance(extensions, dict) else {}
    dimensions = {}
    if isinstance(layout, dict) and isinstance(layout.get("pages"), list):
        for entry in layout["pages"]:
            if isinstance(entry, dict) and type(entry.get("page_idx")) is int:
                width, height = entry.get("width_pt"), entry.get("height_pt")
                if type(width) in (int, float) and type(height) in (int, float) and math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0:
                    dimensions[entry["page_idx"]] = (width, height)
    for page in parsed["pages"]:
        if not isinstance(page, dict) or type(page.get("page_idx")) is not int or page["page_idx"] < 0 or not isinstance(page.get("blocks"), list):
            raise RuntimeError("MinerU returned invalid pages")
        for block in page["blocks"]:
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                raise RuntimeError("MinerU returned invalid page block")
            item = {"type": block["type"], "page_idx": page["page_idx"]}
            bbox = block.get("bbox")
            if bbox is not None and not (isinstance(bbox, list) and len(bbox) == 4 and all(type(v) in (int, float) and 0 <= v <= 1 for v in bbox)):
                raise RuntimeError("MinerU returned invalid block bbox")
            if bbox is not None:
                if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
                    raise RuntimeError("MinerU returned invalid block bbox")
                size = dimensions.get(page["page_idx"])
                if size:
                    width, height = size
                    item["bbox"] = [bbox[0] * width, bbox[1] * height, bbox[2] * width, bbox[3] * height]
                else:
                    item.pop("bbox", None)
            if "content" in block and not isinstance(block["content"], str):
                raise RuntimeError("MinerU returned invalid block content")
            if isinstance(block.get("content"), str):
                item["text"] = block["content"]
            if block["type"] in {"image", "chart", "table"}:
                kind = block["type"]
                source = block.get("image_source")
                if source is not None:
                    if not isinstance(source, str) or PurePosixPath(source).parent != PurePosixPath("images") or "\\" in source:
                        raise RuntimeError("MinerU returned invalid image source")
                    item["img_path"] = source
                for source_key, suffix in (("captions", "caption"), ("footnotes", "footnote")):
                    entries = block.get(source_key, [])
                    if not isinstance(entries, list) or any(not isinstance(entry, dict) or not isinstance(entry.get("content"), str) for entry in entries):
                        raise RuntimeError("MinerU returned invalid captions or footnotes")
                    item[f"{kind}_{suffix}"] = [entry["content"] for entry in entries]
            content_list.append(item)
    return markdown_text, content_list


def _replace_directory(staged: Path, target: Path) -> None:
    """Install with rollback on failure; single writer, not atomic to readers."""
    backup = target.with_name(f".{target.name}.old-{uuid.uuid4().hex}")
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(staged, target)
    except Exception:
        if backup.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _persist_outputs(
    root: Path,
    pdf_path: Path,
    analysis_dir: Path,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, list[dict]]:
    stem = pdf_path.stem
    markdown_text, content_list = _read_outputs(root, stem)

    analysis_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".puba-mineru-stage-", dir=analysis_dir) as tmp:
        staged = Path(tmp) / "mineru"
        staged.mkdir()
        for name in ("markdown.md", "middle_json.json", "structured_content.json"):
            src = _single_file(root, name, required=True)
            assert src is not None
            shutil.copy2(src, staged / name)
        (staged / f"{stem}_content_list.json").write_text(
            json.dumps(content_list, ensure_ascii=False), encoding="utf-8"
        )

        image_dirs = [path for path in root.rglob("images") if path.is_dir()]
        if len(image_dirs) > 1:
            raise RuntimeError("MinerU returned multiple images directories")
        if image_dirs:
            shutil.copytree(image_dirs[0], staged / "images")
        for item in content_list:
            img_path = item.get("img_path")
            if img_path is not None and not (staged / img_path).is_file():
                raise RuntimeError(f"MinerU returned missing image file: {img_path}")
        if metadata is not None:
            (staged / "service.json").write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
            )

        _replace_directory(staged, analysis_dir / "mineru")

    return markdown_text, content_list


_MAX_CONTROL_BYTES = 1024 * 1024


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise httpx.TimeoutException("MinerU service deadline exceeded")
    return remaining


def _check_encoding(response: httpx.Response) -> None:
    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
        raise RuntimeError("MinerU service returned unsupported content encoding")


async def _control_request(client: httpx.AsyncClient, method: str, url: str, deadline: float, **kwargs: Any) -> httpx.Response:
    headers = httpx.Headers(kwargs.pop("headers", {}))
    headers["Accept-Encoding"] = "identity"
    async with client.stream(method, url, timeout=_remaining(deadline), headers=headers, **kwargs) as response:
        _remaining(deadline)
        _check_encoding(response)
        if not response.is_success:
            return httpx.Response(response.status_code, headers=response.headers)
        data = bytearray()
        async for chunk in response.aiter_raw():
            _remaining(deadline)
            data.extend(chunk)
            if len(data) > _MAX_CONTROL_BYTES:
                raise RuntimeError("MinerU service control response is too large")
        _remaining(deadline)
        return httpx.Response(response.status_code, headers=response.headers, content=bytes(data))


async def _upload_chunks(pdf: Any, deadline: float):
    while True:
        _remaining(deadline)
        chunk = pdf.read(65536)
        _remaining(deadline)
        if not chunk:
            return
        yield chunk


def _check_response(response: httpx.Response, action: str) -> None:
    if not response.is_success:
        raise RuntimeError(
            f"MinerU service {action} failed (HTTP {response.status_code})"
        )


async def _get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    deadline: float,
) -> httpx.Response:
    """Retry idempotent service reads on transient transport/server failures."""
    transient_statuses = {408, 429, 500, 502, 503, 504}
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            response = await _control_request(client, "GET", url, deadline)
        except httpx.RequestError:
            if attempt == 2:
                raise
        else:
            if response.status_code not in transient_statuses or attempt == 2:
                return response
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.5 * (attempt + 1), remaining))
    raise httpx.TimeoutException("MinerU service deadline exceeded")


async def _download_result(
    client: httpx.AsyncClient,
    url: str,
    destination: Path,
    deadline: float,
    action: str,
) -> None:
    transient_statuses = {408, 429, 500, 502, 503, 504}
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            async with client.stream("GET", url, timeout=remaining, headers={"Accept-Encoding": "identity"}) as response:
                _remaining(deadline)
                _check_encoding(response)
                if response.status_code in transient_statuses and attempt < 2:
                    continue
                _check_response(response, action)
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > _MAX_ARCHIVE_DOWNLOAD_BYTES:
                    raise RuntimeError("MinerU service ZIP download is too large")
                size = 0
                with destination.open("wb") as output:
                    async for chunk in response.aiter_raw():
                        _remaining(deadline)
                        size += len(chunk)
                        if size > _MAX_ARCHIVE_DOWNLOAD_BYTES:
                            raise RuntimeError("MinerU service ZIP download is too large")
                        output.write(chunk)
                _remaining(deadline)
                return
        except httpx.RequestError:
            if attempt == 2:
                raise
        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(0.5 * (attempt + 1), remaining))
    raise httpx.TimeoutException("MinerU service deadline exceeded")


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    extracted = destination / "extracted"
    extracted.mkdir()
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise RuntimeError("MinerU service returned an invalid ZIP archive") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > _MAX_ARCHIVE_FILES:
            raise RuntimeError("MinerU service ZIP contains too many files")
        if sum(info.file_size for info in infos) > _MAX_ARCHIVE_BYTES:
            raise RuntimeError("MinerU service ZIP is too large when expanded")

        seen: set[PurePosixPath] = set()
        for info in infos:
            member = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if (
                not info.filename
                or "\\" in info.filename
                or member.is_absolute()
                or ".." in member.parts
                or (member.parts and ":" in member.parts[0])
                or stat.S_ISLNK(mode)
                or info.flag_bits & 0x1
            ):
                raise RuntimeError(f"MinerU service ZIP contains unsafe path: {info.filename!r}")
            if member in seen:
                raise RuntimeError(f"MinerU service ZIP contains duplicate path: {info.filename!r}")
            seen.add(member)
            target = extracted.joinpath(*member.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _same_origin_url(base_url: str, supplied: str) -> str:
    if not isinstance(supplied, str) or not supplied:
        raise RuntimeError("MinerU returned an invalid URL")
    resolved = urljoin(base_url + "/", supplied)
    base, target = urlsplit(base_url), urlsplit(resolved)
    if (target.scheme, target.hostname, target.port) != (base.scheme, base.hostname, base.port) or target.username or target.password:
        raise RuntimeError("MinerU returned a URL with a different origin")
    return resolved


async def _network_mineru_service(
    pdf_path: Path, tmp_path: Path, service: dict[str, Any],
) -> dict[str, Any]:
    base_url = str(service.get("base_url", "http://mineru:8000")).rstrip("/")
    timeout_s = float(service.get("timeout_s", 1800))
    poll_interval = float(service.get("poll_interval_s", 2))
    deadline = time.monotonic() + timeout_s
    tier = service.get("tier") or "basic"
    task_id = None
    token = os.environ.get("MINERU_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with asyncio.timeout(_remaining(deadline)):
            async with httpx.AsyncClient(timeout=timeout_s, headers=headers, follow_redirects=False) as client:
                response = await _get_with_retries(client, base_url + "/v1/health", deadline)
                _check_response(response, "health check")
                health = response.json()
                if not isinstance(health, dict) or health.get("status") != "ok":
                    raise RuntimeError("MinerU service is not healthy")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise httpx.TimeoutException("deadline exceeded")
                response = await _control_request(client, "POST", base_url + "/v1/tasks", deadline, json={
                    "tier": tier, "ocr_mode": "auto", "output_formats": ["default"],
                    "files": [{"file_name": pdf_path.name, "bytes": pdf_path.stat().st_size}],
                })
                _check_response(response, "task creation")
                created = response.json()
                if not isinstance(created, dict):
                    raise RuntimeError("MinerU returned invalid task creation response")
                task_id = created.get("task_id")
                files = created.get("files")
                if not isinstance(task_id, str) or not task_id or not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
                    raise RuntimeError("MinerU returned invalid task creation response")
                file_id = files[0].get("file_id")
                upload = files[0].get("upload")
                if not isinstance(file_id, str) or not file_id or not isinstance(upload, dict):
                    raise RuntimeError("MinerU returned invalid upload information")
                upload_url = _same_origin_url(base_url, upload.get("url"))
                upload_headers = upload.get("headers", {})
                if not isinstance(upload_headers, dict) or any(k.lower() not in {"content-type"} or not isinstance(v, str) for k, v in upload_headers.items()):
                    raise RuntimeError("MinerU returned unsupported upload headers")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise httpx.TimeoutException("deadline exceeded")
                with pdf_path.open("rb") as pdf:
                    response = await _control_request(client, "PUT", upload_url, deadline,
                        content=_upload_chunks(pdf, deadline),
                        headers={**upload_headers, "Content-Length": str(pdf_path.stat().st_size)})
                _check_response(response, "upload")
                while True:
                    response = await _get_with_retries(client, base_url + "/v1/tasks/" + quote(task_id, safe=""), deadline)
                    _check_response(response, "task status")
                    status = response.json()
                    if not isinstance(status, dict) or not isinstance(status.get("files"), list) or len(status["files"]) != 1 or not isinstance(status["files"][0], dict):
                        raise RuntimeError("MinerU returned invalid task status")
                    file = status["files"][0]
                    if file.get("file_id") != file_id:
                        raise RuntimeError("MinerU returned a mismatched file id")
                    task_status, file_status = status.get("status"), file.get("status")
                    if task_status in {"failed", "partial", "canceled"} or file_status in {"failed", "canceled"}:
                        raise RuntimeError("MinerU task failed")
                    if task_status == "completed" and file_status == "completed":
                        break
                    if task_status not in {"waiting-file", "pending", "running"} or file_status not in {"waiting-file", "pending", "running", "completed"}:
                        raise RuntimeError("MinerU returned an unknown or inconsistent task status")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise httpx.TimeoutException("deadline exceeded")
                    await asyncio.sleep(min(poll_interval, remaining))
                result_url = _same_origin_url(base_url, file.get("result_url"))
                archive = tmp_path / "result.zip"
                await _download_result(client, result_url, archive, deadline, "result download")
        return {
            "base_url": base_url, "task_id": task_id,
            "version": health.get("version"), "protocol_version": "v1",
            "tier": tier,
        }
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise RuntimeError(f"MinerU service timed out for task {task_id or '(not created)'}") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise RuntimeError(f"MinerU service request or JSON failed for task {task_id or '(not created)'}") from exc


def _run_mineru_service(
    pdf_path: Path, analysis_dir: Path, service: dict[str, Any],
) -> tuple[str, list[dict]]:
    with tempfile.TemporaryDirectory(prefix="puba-mineru-service-") as tmp:
        tmp_path = Path(tmp)
        metadata = asyncio.run(_network_mineru_service(pdf_path, tmp_path, service))
        _safe_extract_zip(tmp_path / "result.zip", tmp_path)
        return _persist_outputs(tmp_path / "extracted", pdf_path, analysis_dir, metadata=metadata)


def run_mineru(pdf_path: Path, analysis_dir: Path) -> tuple[str, list[dict]]:
    """Extract a PDF with the configured MinerU service."""
    from .. import config as cfg

    md_config = cfg.md()
    if md_config.get("extractor", "service") != "service":
        raise RuntimeError("MinerU requires the service extractor")
    service = md_config.get("service", {})
    return _run_mineru_service(pdf_path, analysis_dir, service if isinstance(service, dict) else {})
