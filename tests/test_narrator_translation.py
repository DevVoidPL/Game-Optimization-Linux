from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from types import ModuleType

import pytest

from game_optimization_linux.services.narrator_translation import (
    ARGOS_TRANSLATION_PROVIDER_ID,
    ArgosCTranslate2TranslationProvider,
    _model_layout,
)
from game_optimization_linux.services.narrator_translation_worker import (
    _BpeTokenizer,
    _TranslationEngine,
)


def _model_tree(root: Path, *, nested: bool = False) -> Path:
    package = root / "translate-en_pl-1_9" if nested else root
    (package / "model").mkdir(parents=True)
    (package / "metadata.json").write_text(
        '{"from_code":"en","to_code":"pl","package_version":"1.9"}',
        encoding="utf-8",
    )
    (package / "model" / "config.json").write_text("{}", encoding="utf-8")
    (package / "model" / "model.bin").write_bytes(b"model")
    (package / "bpe.model").write_text("#version: 0.2\n", encoding="utf-8")
    return package


class _Worker:
    def __init__(
        self, model_dir: Path, tokenizer: Path, target_prefix: str = ""
    ) -> None:
        self.model_dir = model_dir
        self.tokenizer = tokenizer
        self.target_prefix = target_prefix
        self.calls: list[tuple[str, int]] = []
        self.cancelled = 0
        self.closed = 0

    def translate(self, text: str, *, beam_size: int) -> str:
        self.calls.append((text, beam_size))
        return "Musimy się stąd wydostać."

    def cancel(self) -> None:
        self.cancelled += 1

    def close(self) -> None:
        self.closed += 1


def test_provider_reports_missing_runtime_and_model(tmp_path: Path) -> None:
    provider = ArgosCTranslate2TranslationProvider(
        component_path=tmp_path,
        runtime_available=lambda: False,
    )
    assert provider.available is False
    assert "runtime" in provider.status_message.casefold()

    provider = ArgosCTranslate2TranslationProvider(
        component_path=tmp_path,
        runtime_available=lambda: True,
    )
    assert provider.available is False
    assert "install" in provider.status_message.casefold()


def test_provider_accepts_argos_package_with_one_enclosing_directory(
    tmp_path: Path,
) -> None:
    package = _model_tree(tmp_path, nested=True)
    assert _model_layout(tmp_path) == (
        package / "model",
        package / "bpe.model",
        "",
    )


def test_provider_translates_on_cpu_worker_and_namespaces_cache_version(
    tmp_path: Path,
) -> None:
    package = _model_tree(tmp_path)
    workers: list[_Worker] = []

    def factory(model_dir: Path, tokenizer: Path, target_prefix: str) -> _Worker:
        worker = _Worker(model_dir, tokenizer, target_prefix)
        workers.append(worker)
        return worker

    times = iter((10.0, 10.125))
    provider = ArgosCTranslate2TranslationProvider(
        component_path=tmp_path,
        runtime_available=lambda: True,
        worker_factory=factory,
        clock=lambda: next(times),
    )
    result = provider.translate(
        "  We   need to get out of here. ",
        source_language="en",
        target_language="pl",
        profile_id="",
    )

    assert provider.available is True
    assert provider.provider_id == ARGOS_TRANSLATION_PROVIDER_ID
    assert "1.9" in provider.provider_id
    assert result.source_text == "We need to get out of here."
    assert result.translated_text == "Musimy się stąd wydostać."
    assert result.provider_id == provider.provider_id
    assert result.elapsed_ms == pytest.approx(125.0)
    assert workers[0].model_dir == package / "model"
    assert workers[0].tokenizer == package / "bpe.model"
    assert workers[0].calls == [("We need to get out of here.", 4)]


def test_fast_profile_uses_greedy_translation_and_worker_is_reused(
    tmp_path: Path,
) -> None:
    _model_tree(tmp_path)
    workers: list[_Worker] = []

    def factory(model_dir: Path, tokenizer: Path, target_prefix: str) -> _Worker:
        worker = _Worker(model_dir, tokenizer, target_prefix)
        workers.append(worker)
        return worker

    provider = ArgosCTranslate2TranslationProvider(
        component_path=tmp_path,
        runtime_available=lambda: True,
        worker_factory=factory,
    )
    for text in ("First line", "Second line"):
        provider.translate(
            text,
            source_language="en",
            target_language="pl",
            profile_id="fast",
        )
    assert len(workers) == 1
    assert workers[0].calls == [("First line", 1), ("Second line", 1)]


def test_provider_rejects_wrong_language_profile_and_empty_text(
    tmp_path: Path,
) -> None:
    _model_tree(tmp_path)
    provider = ArgosCTranslate2TranslationProvider(
        component_path=tmp_path,
        runtime_available=lambda: True,
        worker_factory=_Worker,
    )
    with pytest.raises(ValueError, match="English to Polish"):
        provider.translate(
            "Text", source_language="de", target_language="pl", profile_id=""
        )
    with pytest.raises(ValueError, match="empty"):
        provider.translate(
            "  ", source_language="en", target_language="pl", profile_id=""
        )
    with pytest.raises(ValueError, match="Unsupported"):
        provider.translate(
            "Text",
            source_language="en",
            target_language="pl",
            profile_id="unknown",
        )


def test_cancel_and_close_discard_lazy_worker(tmp_path: Path) -> None:
    _model_tree(tmp_path)
    workers: list[_Worker] = []

    def factory(model_dir: Path, tokenizer: Path, target_prefix: str) -> _Worker:
        worker = _Worker(model_dir, tokenizer, target_prefix)
        workers.append(worker)
        return worker

    provider = ArgosCTranslate2TranslationProvider(
        component_path=tmp_path,
        runtime_available=lambda: True,
        worker_factory=factory,
    )
    provider.translate("One", source_language="en", target_language="pl", profile_id="")
    provider.cancel()
    assert workers[0].cancelled == 1
    provider.translate("Two", source_language="en", target_language="pl", profile_id="")
    provider.close()
    assert workers[1].closed == 1


@dataclass
class _Result:
    hypotheses: list[list[str]]


class _Tokenizer:
    def __init__(self, *, model_file: str) -> None:
        self.model_file = model_file

    def encode(self, text: str, *, out_type: type[str]) -> list[str]:
        assert out_type is str
        return text.split()

    def decode(self, tokens: list[str]) -> str:
        return " ".join(tokens)


class _Translator:
    def __init__(self, model_dir: str, **options) -> None:
        assert model_dir == "/model"
        assert options == {
            "device": "cpu",
            "compute_type": "auto",
            "inter_threads": 1,
            "intra_threads": 0,
        }

    def translate_batch(self, batch, **options):
        assert batch == [["English", "line"]]
        assert options == {
            "beam_size": 4,
            "num_hypotheses": 1,
            "length_penalty": 0.2,
            "replace_unknowns": True,
        }
        return [_Result([["Polska", "kwestia"]])]


def test_worker_engine_uses_cpu_ctranslate2_and_argos_tokenizer_boundary() -> None:
    engine = _TranslationEngine(
        Path("/model"),
        Path("/tokenizer.model"),
        translator_factory=_Translator,
        tokenizer_factory=_Tokenizer,
    )
    assert engine.translate("English line", beam_size=4) == "Polska kwestia"


def test_worker_engine_uses_and_removes_argos_target_prefix() -> None:
    class PrefixedTranslator(_Translator):
        def translate_batch(self, batch, **options):
            assert options["target_prefix"] == [["<2pl>"]]
            return [_Result([["<2pl>", "Polska", "kwestia"]])]

    engine = _TranslationEngine(
        Path("/model"),
        Path("/tokenizer.model"),
        "<2pl>",
        translator_factory=PrefixedTranslator,
        tokenizer_factory=_Tokenizer,
    )
    assert engine.translate("English line", beam_size=4) == "Polska kwestia"


def test_argos_bpe_tokenizer_normalizes_segments_and_detokenizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []

    class Normalizer:
        def __init__(self, language: str) -> None:
            assert language == "en"

        def normalize(self, text: str) -> str:
            calls.append(("normalize", text))
            return text.replace("’", "'")

    class Tokenizer:
        def __init__(self, language: str) -> None:
            assert language == "en"

        def tokenize(self, text: str) -> list[str]:
            calls.append(("tokenize", text))
            return ["We", "can't", "wait", "."]

    class Detokenizer:
        def __init__(self, language: str) -> None:
            assert language == "pl"

        def detokenize(self, words: list[str]) -> str:
            calls.append(("detokenize", words))
            return "Nie możemy czekać."

    class Bpe:
        def __init__(self, stream: object) -> None:
            calls.append(("bpe", stream.read()))

        def segment_tokens(self, tokens: list[str]) -> list[str]:
            calls.append(("segment", tokens))
            return ["We", "can@@", "'t", "wait", "."]

    sacremoses = ModuleType("sacremoses")
    sacremoses.__path__ = []  # type: ignore[attr-defined]
    normalize = ModuleType("sacremoses.normalize")
    normalize.MosesPunctNormalizer = Normalizer  # type: ignore[attr-defined]
    tokenize = ModuleType("sacremoses.tokenize")
    tokenize.MosesTokenizer = Tokenizer  # type: ignore[attr-defined]
    tokenize.MosesDetokenizer = Detokenizer  # type: ignore[attr-defined]
    subword_nmt = ModuleType("subword_nmt")
    subword_nmt.__path__ = []  # type: ignore[attr-defined]
    apply_bpe = ModuleType("subword_nmt.apply_bpe")
    apply_bpe.BPE = Bpe  # type: ignore[attr-defined]
    for name, module in {
        "sacremoses": sacremoses,
        "sacremoses.normalize": normalize,
        "sacremoses.tokenize": tokenize,
        "subword_nmt": subword_nmt,
        "subword_nmt.apply_bpe": apply_bpe,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    model = tmp_path / "bpe.model"
    model.write_text("#version: 0.2\n", encoding="utf-8")

    tokenizer = _BpeTokenizer(str(model))

    assert tokenizer.encode("We can’t wait.", out_type=str) == [
        "We",
        "can@@",
        "'t",
        "wait",
        ".",
    ]
    assert tokenizer.decode(["Nie", "mo@@", "żemy", "czekać", "."]) == (
        "Nie możemy czekać."
    )
    assert ("bpe", "#version: 0.2\n") in calls
