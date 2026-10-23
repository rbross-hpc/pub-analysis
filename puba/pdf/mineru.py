# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Local and service-backed MinerU PDF extraction."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx


_INSTALL_HINT = (
    "MinerU not installed or not on PATH. "
    "Install puba with the local extractor: pip install 'puba[local-mineru]'"
)

_INTERMEDIATE_FILES = (
    "{stem}.md",
    "{stem}_content_list.json",
    "{stem}_content_list_v2.json",
    "{stem}_middle.json",
    "{stem}_layout.pdf",
)

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
    md_path = _single_file(root, f"{stem}.md", required=True)
    assert md_path is not None
    try:
        markdown_text = md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"MinerU returned non-UTF-8 markdown: {md_path.name}") from exc

    content_list: list[dict] = []
    cl_path = _single_file(root, f"{stem}_content_list.json")
    if cl_path is not None:
        try:
            parsed = json.loads(cl_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"MinerU returned invalid content list: {cl_path.name}") from exc
        if not isinstance(parsed, list):
            raise RuntimeError(f"MinerU content list must be a JSON list: {cl_path.name}")
        if not all(isinstance(block, dict) for block in parsed):
            raise RuntimeError(f"MinerU content list entries must be JSON objects: {cl_path.name}")
        content_list = parsed
    return markdown_text, content_list


def _replace_directory(staged: Path, target: Path) -> None:
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
        for pattern in _INTERMEDIATE_FILES:
            name = pattern.format(stem=stem)
            src = _single_file(root, name)
            if src is not None:
                shutil.copy2(src, staged / name)

        image_dirs = [path for path in root.rglob("images") if path.is_dir()]
        if len(image_dirs) > 1:
            raise RuntimeError("MinerU returned multiple images directories")
        if image_dirs:
            shutil.copytree(image_dirs[0], staged / "images")
        if metadata is not None:
            (staged / "service.json").write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
            )

        _replace_directory(staged, analysis_dir / "mineru")

    return markdown_text, content_list


def _run_mineru_local(pdf_path: Path, analysis_dir: Path) -> tuple[str, list[dict]]:
    if not shutil.which("mineru"):
        raise RuntimeError(_INSTALL_HINT)

    with tempfile.TemporaryDirectory(prefix="puba-mineru-") as tmp:
        tmp_path = Path(tmp)
        cmd = [
            "mineru",
            "-p", str(pdf_path),
            "-o", str(tmp_path),
            "-b", "pipeline",
            "-f", "false",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            stderr_tail = result.stderr.strip().splitlines()
            tail = "\n".join(stderr_tail[-20:]) if stderr_tail else "(no stderr)"
            raise RuntimeError(
                f"MinerU failed (exit {result.returncode}) on {pdf_path.name}:\n{tail}"
            )

        return _persist_outputs(tmp_path, pdf_path, analysis_dir)


def _response_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if detail:
            return str(detail)
    except (ValueError, UnicodeDecodeError):
        pass
    text = response.text.strip()
    return text[:500] if text else response.reason_phrase


def _check_response(response: httpx.Response, action: str) -> None:
    if response.is_error:
        raise RuntimeError(
            f"MinerU service {action} failed (HTTP {response.status_code}): "
            f"{_response_error(response)}"
        )


def _get_with_retries(
    client: httpx.Client,
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
            response = client.get(url, timeout=remaining)
        except httpx.RequestError:
            if attempt == 2:
                raise
        else:
            if response.status_code not in transient_statuses or attempt == 2:
                return response
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.5 * (attempt + 1), remaining))
    raise httpx.TimeoutException(f"MinerU service deadline exceeded while reading {url}")


def _download_result(
    client: httpx.Client,
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
            with client.stream("GET", url, timeout=remaining) as response:
                if response.status_code in transient_statuses and attempt < 2:
                    continue
                if response.is_error:
                    response.read()
                    _check_response(response, action)
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > _MAX_ARCHIVE_DOWNLOAD_BYTES:
                    raise RuntimeError("MinerU service ZIP download is too large")
                size = 0
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > _MAX_ARCHIVE_DOWNLOAD_BYTES:
                            raise RuntimeError("MinerU service ZIP download is too large")
                        output.write(chunk)
                return
        except httpx.RequestError:
            if attempt == 2:
                raise
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.5 * (attempt + 1), remaining))
    raise httpx.TimeoutException(f"MinerU service deadline exceeded while reading {url}")


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


def _run_mineru_service(
    pdf_path: Path,
    analysis_dir: Path,
    service: dict[str, Any],
) -> tuple[str, list[dict]]:
    base_url = str(service.get("base_url", "http://host-localhost:8001")).rstrip("/")
    timeout_s = service.get("timeout_s", 1800)
    poll_interval_s = service.get("poll_interval_s", 2)
    deadline = time.monotonic() + float(timeout_s)
    request_timeout = httpx.Timeout(float(timeout_s), connect=min(10.0, float(timeout_s)))
    fields = {
        "backend": "pipeline",
        "parse_method": "auto",
        "formula_enable": "false",
        "table_enable": "true",
        "return_md": "true",
        "return_middle_json": "true",
        "return_model_output": "false",
        "return_content_list": "true",
        "return_images": "true",
        "response_format_zip": "true",
        "return_original_file": "false",
        "client_side_output_generation": "false",
    }

    task_id: str | None = None
    with tempfile.TemporaryDirectory(prefix="puba-mineru-service-") as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / "result.zip"
        try:
            with httpx.Client(timeout=request_timeout) as client, pdf_path.open("rb") as pdf_file:
                response = _get_with_retries(client, f"{base_url}/health", deadline)
                _check_response(response, "health check")
                try:
                    health = response.json()
                except ValueError as exc:
                    raise RuntimeError("MinerU service returned invalid health JSON") from exc
                if not isinstance(health, dict) or health.get("status") != "healthy":
                    raise RuntimeError("MinerU service is not healthy")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise httpx.TimeoutException("MinerU service deadline exceeded before submission")
                response = client.post(
                    f"{base_url}/tasks",
                    data=fields,
                    files={"files": (pdf_path.name, pdf_file, "application/pdf")},
                    timeout=remaining,
                )
                _check_response(response, "submission")
                try:
                    submitted = response.json()
                except ValueError as exc:
                    raise RuntimeError("MinerU service returned invalid submission JSON") from exc
                task_id = submitted.get("task_id") if isinstance(submitted, dict) else None
                if not isinstance(task_id, str) or not task_id:
                    raise RuntimeError("MinerU service submission did not return a task_id")

                while True:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"MinerU service task {task_id} timed out after {timeout_s} seconds"
                        )
                    response = _get_with_retries(
                        client, f"{base_url}/tasks/{task_id}", deadline
                    )
                    _check_response(response, f"task {task_id} status")
                    try:
                        status_payload = response.json()
                    except ValueError as exc:
                        raise RuntimeError(
                            f"MinerU service returned invalid status JSON for task {task_id}"
                        ) from exc
                    status_value = (
                        status_payload.get("status")
                        if isinstance(status_payload, dict)
                        else None
                    )
                    if status_value == "completed":
                        break
                    if status_value in {"failed", "error"}:
                        detail = status_payload.get("error") or "unknown service error"
                        raise RuntimeError(f"MinerU service task {task_id} failed: {detail}")
                    if status_value not in {"pending", "processing"}:
                        raise RuntimeError(
                            f"MinerU service task {task_id} returned unknown status: {status_value!r}"
                        )
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            f"MinerU service task {task_id} timed out after {timeout_s} seconds"
                        )
                    time.sleep(min(float(poll_interval_s), remaining))

                _download_result(
                    client,
                    f"{base_url}/tasks/{task_id}/result",
                    archive_path,
                    deadline,
                    f"task {task_id} result download",
                )
        except httpx.TimeoutException as exc:
            suffix = f" for task {task_id}" if task_id else ""
            raise RuntimeError(f"MinerU service timed out{suffix}: {base_url}") from exc
        except httpx.RequestError as exc:
            suffix = f" for task {task_id}" if task_id else ""
            raise RuntimeError(f"MinerU service request failed{suffix}: {base_url}: {exc}") from exc

        _safe_extract_zip(archive_path, tmp_path)
        metadata = {
            "base_url": base_url,
            "task_id": task_id,
            "version": health.get("version"),
            "protocol_version": health.get("protocol_version"),
        }
        return _persist_outputs(
            tmp_path / "extracted", pdf_path, analysis_dir, metadata=metadata
        )


def run_mineru(pdf_path: Path, analysis_dir: Path) -> tuple[str, list[dict]]:
    """Extract a PDF and persist normalized MinerU intermediates.

    The configured service and local subprocess extractors both produce the
    same ``analysis_dir/mineru`` layout and return ``(markdown, content_list)``.
    """
    from .. import config as cfg

    md_config = cfg.md()
    extractor = str(md_config.get("extractor", "service"))
    if extractor == "service":
        service = md_config.get("service", {})
        return _run_mineru_service(
            pdf_path,
            analysis_dir,
            service if isinstance(service, dict) else {},
        )
    if extractor == "local":
        return _run_mineru_local(pdf_path, analysis_dir)
    raise RuntimeError(f"Unknown MinerU extractor: {extractor!r}")
