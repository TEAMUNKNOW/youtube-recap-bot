"""Helpers for safely chunking long TTS text and merging generated audio."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Iterable


def split_text(text: str, max_chars: int) -> list[str]:
    """Split on sentence/word boundaries without dropping any text."""
    text = " ".join(text.split())
    if not text:
        return []
    parts: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = max(
            remaining.rfind(". ", 0, max_chars),
            remaining.rfind("? ", 0, max_chars),
            remaining.rfind("! ", 0, max_chars),
            remaining.rfind(" ", 0, max_chars),
        )
        if cut < max_chars // 2:
            cut = max_chars
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


async def merge_mp3s(parts: Iterable[Path], output: Path) -> float:
    paths = [Path(p) for p in parts]
    if not paths:
        raise ValueError("No audio parts to merge")
    if len(paths) == 1:
        paths[0].replace(output)
    else:
        list_file = output.with_suffix(".concat.txt")
        try:
            lines = []
            for p in paths:
                safe = str(p.resolve()).replace("'", "'\\''")
                lines.append(f"file '{safe}'")
            list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c:a", "libmp3lame", "-b:a", "128k", str(output),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Audio merge failed: {err.decode(errors='replace')[-1000:]}")
        finally:
            list_file.unlink(missing_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(output),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0


async def synthesize_chunked(
    text: str,
    output_path: Path,
    *,
    max_chars: int,
    concurrency: int,
    synthesize_one: Callable[[str, Path], Awaitable[None]],
) -> float:
    chunks = split_text(text, max_chars)
    if not chunks:
        raise ValueError("Empty text")
    if len(chunks) == 1:
        await synthesize_one(chunks[0], output_path)
        return await _probe(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_dir = output_path.parent / f".{output_path.stem}_parts"
    part_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(i: int, chunk: str) -> Path:
        path = part_dir / f"part_{i:04d}.mp3"
        async with sem:
            await synthesize_one(chunk, path)
        return path

    try:
        parts = await asyncio.gather(*(one(i, chunk) for i, chunk in enumerate(chunks)))
        return await merge_mp3s(parts, output_path)
    finally:
        for p in part_dir.glob("*"):
            p.unlink(missing_ok=True)
        part_dir.rmdir()


async def _probe(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0
