from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

from game_optimization_linux.models.narrator import CaptureFrame
from game_optimization_linux.services.narrator_ocr import (
    TESSERACT_COMPONENT_ID,
    TESSERACT_MODEL_RELATIVE_PATH,
    TESSERACT_POLISH_COMPONENT_ID,
    TESSERACT_POLISH_MODEL_RELATIVE_PATH,
    TesseractOcrProvider,
    TesseractWorkerClient,
)
from game_optimization_linux.services.narrator_ocr_worker import (
    TSV_HEADER,
    handle_request,
    normalize_tsv,
)


_TSV_BODY = "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t92\t{word}\n"


def _frame() -> CaptureFrame:
    width = 80
    height = 24
    row = bytes(round(column * 255 / (width - 1)) for column in range(width))
    return CaptureFrame(
        session_id="worker",
        generation=1,
        timestamp_monotonic=1.0,
        width=width,
        height=height,
        stride=width,
        pixel_format="gray8",
        pixels=row * height,
    )


def _models(root: Path) -> None:
    english = root / TESSERACT_COMPONENT_ID / TESSERACT_MODEL_RELATIVE_PATH
    polish = (
        root
        / TESSERACT_POLISH_COMPONENT_ID
        / TESSERACT_POLISH_MODEL_RELATIVE_PATH
    )
    english.parent.mkdir(parents=True)
    polish.parent.mkdir(parents=True)
    english.write_bytes(b"english model fixture")
    polish.write_bytes(b"polish model fixture")


class _FakeWorker:
    def __init__(self, language: str, *, failures: int = 0) -> None:
        self.language = language
        self.failures = failures
        self.calls = 0
        self.cancelled = False
        self.closed = False
        self.resolutions: list[int] = []
        self.start_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def recognize_png(self, payload: bytes, *, source_resolution: int) -> str:
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        self.calls += 1
        self.resolutions.append(source_resolution)
        if self.calls <= self.failures:
            raise RuntimeError("simulated worker crash")
        word = "Czesc" if self.language == "pol" else "Hello"
        return normalize_tsv(_TSV_BODY.format(word=word))

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True


def _provider(root: Path, factory, *, runner=None) -> TesseractOcrProvider:
    values = {
        "executable": "/fixture/tesseract",
        "worker_factory": factory,
        "persistent_worker_enabled": True,
    }
    if runner is not None:
        values["runner"] = runner
    return TesseractOcrProvider(root, **values)


def test_persistent_worker_is_initialized_once_and_reused(tmp_path: Path) -> None:
    _models(tmp_path)
    workers: list[_FakeWorker] = []

    def factory(model: Path, language: str) -> _FakeWorker:
        assert model.name == "eng.traineddata"
        worker = _FakeWorker(language)
        workers.append(worker)
        return worker

    provider = _provider(tmp_path, factory)

    first = provider.recognize(_frame(), language="en")
    second = provider.recognize(_frame(), language="en")

    assert first.text == second.text == "Hello"
    assert len(workers) == 1
    assert workers[0].calls == 2
    # 96, not 100. The value is derived from the image's DPI metadata by
    # TesseractOcrProvider._source_resolution, and nothing in the codebase ever
    # calls setDotsPerMeter: CaptureFrame carries no DPI field, so every QImage
    # keeps Qt's default dotsPerMeterX of 3780, which is exactly 96 DPI.
    # QImage.scaled() copies that metadata unchanged, so the ~2x preprocessing
    # upscale does not alter it either. 96 is also what the older CLI path fed
    # Tesseract, because Qt's PNG encoder writes a pHYs chunk of 3780 dpm and
    # Tesseract read the DPI from the file. Do not "restore" 100: no code path
    # produces it.
    assert workers[0].resolutions == [96, 96]


def test_prepare_warms_selected_model_once_before_repeated_ocr(tmp_path: Path) -> None:
    _models(tmp_path)
    workers: list[_FakeWorker] = []

    def factory(model: Path, language: str) -> _FakeWorker:
        assert model.name == "pol.traineddata"
        worker = _FakeWorker(language)
        workers.append(worker)
        return worker

    provider = _provider(tmp_path, factory)
    provider.prepare("pl")
    provider.recognize(_frame(), language="pl")
    provider.recognize(_frame(), language="pl")

    assert len(workers) == 1
    assert workers[0].start_calls == 1
    assert workers[0].calls == 2


@pytest.mark.parametrize(
    ("language", "model_name", "expected"),
    (("en", "eng.traineddata", "Hello"), ("pl", "pol.traineddata", "Czesc")),
)
def test_persistent_worker_uses_selected_language_only(
    tmp_path: Path, language: str, model_name: str, expected: str
) -> None:
    _models(tmp_path)
    created: list[tuple[str, str]] = []

    def factory(model: Path, tesseract_language: str) -> _FakeWorker:
        created.append((model.name, tesseract_language))
        return _FakeWorker(tesseract_language)

    provider = _provider(tmp_path, factory)

    result = provider.recognize(_frame(), language=language)

    assert result.text == expected
    assert created == [(model_name, "pol" if language == "pl" else "eng")]


def test_language_switch_closes_previous_worker_and_keeps_one_model(
    tmp_path: Path,
) -> None:
    _models(tmp_path)
    workers: list[_FakeWorker] = []

    def factory(model: Path, language: str) -> _FakeWorker:
        del model
        worker = _FakeWorker(language)
        workers.append(worker)
        return worker

    provider = _provider(tmp_path, factory)
    provider.recognize(_frame(), language="en")
    provider.recognize(_frame(), language="pl")

    assert len(workers) == 2
    assert workers[0].closed is True
    assert workers[1].closed is False
    assert provider._worker is workers[1]


def test_worker_failure_is_restarted_before_cli_fallback(tmp_path: Path) -> None:
    _models(tmp_path)
    workers: list[_FakeWorker] = []

    def factory(model: Path, language: str) -> _FakeWorker:
        del model
        worker = _FakeWorker(language, failures=1 if not workers else 0)
        workers.append(worker)
        return worker

    provider = _provider(tmp_path, factory)

    result = provider.recognize(_frame(), language="en")

    assert result.text == "Hello"
    assert len(workers) == 2
    assert workers[0].closed is True
    assert workers[1].calls == 1


def test_two_worker_failures_use_existing_cli_path(tmp_path: Path) -> None:
    _models(tmp_path)
    workers: list[_FakeWorker] = []
    cli_calls: list[list[str]] = []

    def factory(model: Path, language: str) -> _FakeWorker:
        del model
        worker = _FakeWorker(language, failures=1)
        workers.append(worker)
        return worker

    def runner(argv, **values):
        assert values["input"].startswith(b"\x89PNG\r\n\x1a\n")
        cli_calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, (TSV_HEADER + _TSV_BODY.format(word="Fallback")).encode(), b""
        )

    provider = _provider(tmp_path, factory, runner=runner)

    result = provider.recognize(_frame(), language="en")

    assert result.text == "Fallback"
    assert len(workers) == 2
    assert len(cli_calls) == 1
    assert "--oem" in cli_calls[0] and "1" in cli_calls[0]
    assert "--psm" in cli_calls[0] and "6" in cli_calls[0]


def test_headerless_c_api_tsv_normalizes_to_cli_shape() -> None:
    body = _TSV_BODY.format(word="Hello")

    assert normalize_tsv(body) == TSV_HEADER + body
    assert normalize_tsv(TSV_HEADER + body) == TSV_HEADER + body


def test_worker_and_cli_tsv_have_equivalent_text_and_confidence(
    tmp_path: Path,
) -> None:
    _models(tmp_path)
    body = _TSV_BODY.format(word="Hello")
    worker_provider = _provider(
        tmp_path, lambda model, language: _FakeWorker(language)
    )
    cli_provider = TesseractOcrProvider(
        tmp_path,
        executable="/fixture/tesseract",
        runner=lambda argv, **values: subprocess.CompletedProcess(
            argv, 0, (TSV_HEADER + body).encode(), b""
        ),
    )

    worker_result = worker_provider.recognize(_frame(), language="en")
    cli_text, cli_confidence = cli_provider._parse_tsv(TSV_HEADER + body)

    assert worker_result.text == cli_text
    assert worker_result.confidence == pytest.approx(cli_confidence)


def test_cancel_and_close_clean_up_persistent_workers(tmp_path: Path) -> None:
    _models(tmp_path)
    cancelled = _FakeWorker("eng")
    provider = _provider(tmp_path, lambda model, language: cancelled)
    provider.recognize(_frame(), language="en")
    provider.cancel()

    assert cancelled.cancelled is True
    assert provider._worker is None

    closed = _FakeWorker("eng")
    provider = _provider(tmp_path, lambda model, language: closed)
    provider.recognize(_frame(), language="en")
    provider.close()

    assert closed.closed is True
    assert provider._worker is None


def test_worker_request_normalizes_headerless_tsv_and_preserves_resolution() -> None:
    class Engine:
        def __init__(self) -> None:
            self.resolution = 0

        def recognize_png(self, payload: bytes, *, source_resolution: int) -> str:
            assert payload == b"\x89PNG\r\n\x1a\nfixture"
            self.resolution = source_resolution
            return normalize_tsv(_TSV_BODY.format(word="Hello"))

    engine = Engine()
    response = handle_request(
        {
            "command": "recognize",
            "request_id": 7,
            "png_base64": base64.b64encode(
                b"\x89PNG\r\n\x1a\nfixture"
            ).decode("ascii"),
            "source_resolution": 144,
        },
        engine,  # type: ignore[arg-type]
    )

    assert response["request_id"] == 7
    assert response["tsv"] == TSV_HEADER + _TSV_BODY.format(word="Hello")
    assert engine.resolution == 144


def test_client_reuses_one_isolated_process(tmp_path: Path) -> None:
    model = tmp_path / "eng.traineddata"
    model.write_bytes(b"fixture")
    worker_script = tmp_path / "fake_worker.py"
    worker_script.write_text(
        """\
import argparse, json, pathlib, sys
p = argparse.ArgumentParser(add_help=False)
p.add_argument('--model', required=True)
p.add_argument('--language', required=True)
v = p.parse_args()
counter = pathlib.Path(v.model).with_name('initializations')
value = str(int(counter.read_text() or '0') + 1) if counter.exists() else '1'
counter.write_text(value)
print(json.dumps({'status': 'ready', 'initialization_ms': 12.5}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request.get('command') == 'shutdown':
        break
    body = '5\\t1\\t1\\t1\\t1\\t1\\t0\\t0\\t20\\t10\\t92\\tHello\\n'
    header = ('level\\tpage_num\\tblock_num\\tpar_num\\tline_num\\tword_num\\t'
              'left\\ttop\\twidth\\theight\\tconf\\ttext\\n')
    response = {'request_id': request['request_id'], 'ok': True, 'tsv': header + body}
    print(json.dumps(response), flush=True)
""",
        encoding="utf-8",
    )
    client = TesseractWorkerClient(
        model,
        "eng",
        worker_script=worker_script,
    )

    first = client.recognize_png(b"png one", source_resolution=96)
    process = client._process
    second = client.recognize_png(b"png two", source_resolution=96)
    client.close()

    assert first == second
    assert (tmp_path / "initializations").read_text() == "1"
    assert client.initialization_ms == pytest.approx(12.5)
    assert process is not None and process.poll() == 0
