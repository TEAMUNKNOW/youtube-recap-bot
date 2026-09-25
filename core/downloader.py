"""Async yt-dlp media downloader with validation, progress, and cancellation."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from bot.config import Settings
from bot.exceptions import DownloadError, InputError, TimeoutError_

logger = logging.getLogger(__name__)

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "music.youtube.com",
}

DIRECT_MEDIA_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mp3", ".wav", ".m4a"}


def validate_url(url: str, *, allow_direct: bool = True) -> str:
    url = url.strip()
    if not url:
        raise InputError("Empty URL")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InputError("Only http/https URLs are allowed")
    host = (parsed.hostname or "").lower()
    if not host:
        raise InputError("Invalid URL host")

    if host in YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        return url

    if allow_direct:
        path_lower = (parsed.path or "").lower()
        if any(path_lower.endswith(ext) for ext in DIRECT_MEDIA_EXTENSIONS):
            return url
        return url

    raise InputError("Unsupported URL domain")


class DownloadProgress:
    def __init__(self) -> None:
        self.percent: float = 0.0
        self.speed: str = ""
        self.eta: str = ""
        self.status: str = "starting"


class Downloader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass

    async def download(
        self,
        url: str,
        output_dir: Path,
        *,
        progress_cb: Optional[Callable[[DownloadProgress], Any]] = None,
        filename_template: str = "input.%(ext)s",
        max_filesize_bytes: Optional[int] = None,
    ) -> Path:
        url = validate_url(url)
        output_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(output_dir / filename_template)

        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--newline",
            "--print",
            "after_move:filepath",
            "-f",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            outtmpl,
            "--socket-timeout",
            str(self.settings.ytdlp_socket_timeout),
            "--retries",
            str(self.settings.ytdlp_retries),
            "--max-filesize",
            str(max_filesize_bytes if max_filesize_bytes is not None else self.settings.max_input_bytes()),
            "--max-downloads",
            "1",
        ]

        if self.settings.proxy_url:
            cmd.extend(["--proxy", self.settings.proxy_url])
        if self.settings.cookie_file and self.settings.cookie_file.exists():
            cmd.extend(["--cookies", str(self.settings.cookie_file)])
        if self.settings.po_token:
            cmd.extend(["--extractor-args", f"youtube:po_token={self.settings.po_token}"])

        cmd.extend(
            [
                "--progress",
                "--progress-template",
                "download:%(progress.downloaded_bytes)s/%(progress.total_bytes)s/%(progress.speed)s",
            ]
        )
        cmd.append(url)

        logger.info("Starting yt-dlp download for task workspace %s", output_dir)
        progress = DownloadProgress()
        downloaded_path: Optional[Path] = None

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise DownloadError(
                "yt-dlp not found. Install yt-dlp in PATH.",
                retryable=False,
            ) from exc

        assert self._proc.stdout and self._proc.stderr

        async def _read_stdout() -> None:
            nonlocal downloaded_path
            assert self._proc and self._proc.stdout
            async for raw in self._proc.stdout:
                if self._cancelled:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("download:"):
                    parts = line[len("download:") :].split("/")
                    if len(parts) >= 2:
                        try:
                            done = float(parts[0] or 0)
                            total = float(parts[1] or 0)
                            if total > 0:
                                progress.percent = min(100.0, done / total * 100)
                            if len(parts) >= 3:
                                progress.speed = parts[2]
                            if progress_cb:
                                maybe = progress_cb(progress)
                                if asyncio.iscoroutine(maybe):
                                    await maybe
                        except ValueError:
                            pass
                elif Path(line).exists() or line.endswith((".mp4", ".mkv", ".webm", ".m4a")):
                    candidate = Path(line)
                    if candidate.exists():
                        downloaded_path = candidate

        async def _read_stderr() -> str:
            assert self._proc and self._proc.stderr
            chunks: list[str] = []
            async for raw in self._proc.stderr:
                text = raw.decode("utf-8", errors="replace")
                chunks.append(text)
                m = re.search(r"(\d+\.?\d*)%", text)
                if m:
                    try:
                        progress.percent = float(m.group(1))
                        if progress_cb:
                            maybe = progress_cb(progress)
                            if asyncio.iscoroutine(maybe):
                                await maybe
                    except ValueError:
                        pass
            return "".join(chunks)

        try:
            stdout_task = asyncio.create_task(_read_stdout())
            stderr_task = asyncio.create_task(_read_stderr())
            try:
                await asyncio.wait_for(
                    self._proc.wait(),
                    timeout=self.settings.task_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                self.cancel()
                raise TimeoutError_("Download timed out") from exc

            await stdout_task
            stderr_text = await stderr_task
        finally:
            if self._proc and self._proc.returncode is None:
                self.cancel()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except (asyncio.TimeoutError, ProcessLookupError):
                    if self._proc:
                        self._proc.kill()

        if self._cancelled:
            raise DownloadError("Download cancelled", retryable=False)

        if self._proc.returncode != 0:
            safe_err = re.sub(r"(cookie|token|auth|key)[^\s]*", "[REDACTED]", stderr_text, flags=re.I)
            logger.error("yt-dlp failed (code %s): %s", self._proc.returncode, safe_err[-2000:])
            retryable = self._proc.returncode in (1, 2) or "HTTP Error 429" in stderr_text
            raise DownloadError(
                f"yt-dlp exited with code {self._proc.returncode}",
                retryable=retryable,
                details={"stderr_tail": safe_err[-500:]},
            )

        if downloaded_path is None or not downloaded_path.exists():
            candidates = sorted(
                list(output_dir.glob("*.mp4"))
                + list(output_dir.glob("*.mkv"))
                + list(output_dir.glob("*.webm"))
                + list(output_dir.glob("*.m4a")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                raise DownloadError("Download completed but no media file found", retryable=False)
            downloaded_path = candidates[0]

        size = downloaded_path.stat().st_size
        if size > self.settings.max_input_bytes():
            downloaded_path.unlink(missing_ok=True)
            raise DownloadError("Downloaded file exceeds size limit", retryable=False)

        progress.percent = 100.0
        if progress_cb:
            maybe = progress_cb(progress)
            if asyncio.iscoroutine(maybe):
                await maybe

        logger.info("Downloaded %s (%s bytes)", downloaded_path.name, size)
        return downloaded_path
