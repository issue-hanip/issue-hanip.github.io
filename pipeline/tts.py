"""문장 → 음성(mp3). 공급자는 config [shorts].tts_provider 로 선택."""
import asyncio
import re
import subprocess

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


class TTSError(RuntimeError):
    pass


def duration(path) -> float:
    """ffmpeg 출력에서 길이(초)를 읽는다."""
    proc = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if not m:
        raise TTSError(f"길이를 읽을 수 없음: {path}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def _edge(text: str, voice: str, path):
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, voice, rate="+8%").save(str(path))
    asyncio.run(go())


def _gtts(text: str, voice: str, path):
    from gtts import gTTS

    gTTS(text, lang="ko").save(str(path))


def _silent(text: str, voice: str, path):
    secs = max(2.0, len(text) / 7)  # 한국어 낭독 속도 근사
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", f"{secs:.2f}", "-q:a", "9", str(path)], check=True)


# 모두 무료. auto = edge 실패 시 gtts로 대체
PROVIDERS = {"edge": _edge, "gtts": _gtts, "silent": _silent}
CHAINS = {"auto": ["edge", "gtts"]}


def speak_words(text: str, path, provider: str, voice: str) -> tuple[float, list | None]:
    """음성 + 단어별 시각 [(시작초, 길이초, 단어)]. edge-tts만 시각을 주고, 실패하면 시각 없이 speak()."""
    if provider in ("auto", "edge"):
        try:
            import edge_tts

            words = []

            async def go():
                c = edge_tts.Communicate(text, voice, rate="+8%", boundary="WordBoundary")
                with open(path, "wb") as f:
                    async for ch in c.stream():
                        if ch["type"] == "audio":
                            f.write(ch["data"])
                        elif ch["type"] == "WordBoundary":
                            words.append((ch["offset"] / 1e7, ch["duration"] / 1e7, ch["text"]))
            asyncio.run(go())
            return duration(path), words or None
        except Exception:  # noqa: BLE001 - 시각 없이 다시
            pass
    return speak(text, path, provider, voice), None


def speak(text: str, path, provider: str, voice: str) -> float:
    chain = CHAINS.get(provider, [provider])
    errors = []
    for name in chain:
        fn = PROVIDERS.get(name)
        if not fn:
            raise TTSError(f"알 수 없는 TTS 공급자: {name}")
        try:
            fn(text, voice, path)
            return duration(path)
        except Exception as e:  # noqa: BLE001 - 다음 공급자로 넘어감
            errors.append(f"{name}: {e}")
    raise TTSError("; ".join(errors))
