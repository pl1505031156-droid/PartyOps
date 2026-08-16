"""DEB/RPM 安装后自检的成功与硬失败分支。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import package_selftest
from app.database import db_runtime


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "PartyOps"
    frontend = runtime / "_internal" / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text(
        '<script src="assets/app.js"></script>', encoding="utf-8"
    )
    (frontend / "assets").mkdir()
    (frontend / "assets" / "app.js").write_text("// ok", encoding="utf-8")
    (runtime / "ocr" / "bin").mkdir(parents=True)
    (runtime / "ocr" / "tessdata").mkdir(parents=True)
    (runtime / "ocr" / "bin" / "tesseract").write_bytes(b"binary")
    (runtime / "ocr" / "tessdata" / "chi_sim.traineddata").write_bytes(b"data")
    (runtime / "llama-server").write_bytes(b"binary")
    return runtime


def test_runtime_contents_supports_onefile_and_onedir(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    assert package_selftest._runtime_contents(runtime) == runtime
    (runtime / "_internal").mkdir()
    assert package_selftest._runtime_contents(runtime) == runtime / "_internal"


def test_selftest_rejects_missing_frontend_assets_and_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "empty"
    runtime.mkdir()
    with pytest.raises(RuntimeError, match="前端入口缺失"):
        package_selftest.run_selftest(runtime)

    runtime = _runtime(tmp_path)
    (runtime / "_internal" / "frontend" / "assets" / "app.js").unlink()
    with pytest.raises(RuntimeError, match="前端静态资源缺失"):
        package_selftest.run_selftest(runtime)

    (runtime / "_internal" / "frontend" / "assets" / "app.js").write_text(
        "// ok", encoding="utf-8"
    )
    monkeypatch.setattr(
        db_runtime,
        "validate_capabilities",
        lambda: {"safe_version": False, "fts5": True},
    )
    with pytest.raises(RuntimeError, match="SQLite 安全版本或 FTS5"):
        package_selftest.run_selftest(runtime)


def test_selftest_rejects_incomplete_or_unusable_native_runtimes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(
        db_runtime,
        "validate_capabilities",
        lambda: {"safe_version": True, "fts5": True},
    )
    (runtime / "ocr" / "tessdata" / "chi_sim.traineddata").unlink()
    with pytest.raises(RuntimeError, match="中文 OCR 运行时不完整"):
        package_selftest.run_selftest(runtime)

    (runtime / "ocr" / "tessdata" / "chi_sim.traineddata").write_bytes(b"data")
    monkeypatch.setattr(
        package_selftest.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    with pytest.raises(RuntimeError, match="中文 OCR 语言包无法加载"):
        package_selftest.run_selftest(runtime)

    monkeypatch.setattr(
        package_selftest.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="eng\n"),
    )
    with pytest.raises(RuntimeError, match="中文 OCR 语言包无法加载"):
        package_selftest.run_selftest(runtime)


def test_selftest_validates_smart_runtime_and_llama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(
        db_runtime,
        "validate_capabilities",
        lambda: {"safe_version": True, "fts5": True},
    )
    monkeypatch.setattr(
        package_selftest.importlib,
        "import_module",
        lambda name: SimpleNamespace(__version__="unknown" if name == "tokenizers" else "1.0"),
    )
    results = iter(
        [
            SimpleNamespace(returncode=0, stdout="eng\nchi_sim\n"),
            SimpleNamespace(returncode=1, stdout=""),
        ]
    )
    monkeypatch.setattr(
        package_selftest.subprocess, "run", lambda *_args, **_kwargs: next(results)
    )
    with pytest.raises(RuntimeError, match="本地 LLM 运行时无法启动"):
        package_selftest.run_selftest(runtime)

    (runtime / "llama-server").unlink()
    monkeypatch.setattr(
        package_selftest.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="eng\nchi_sim\n"),
    )
    with pytest.raises(RuntimeError, match="本地 LLM 运行时缺失"):
        package_selftest.run_selftest(runtime)


def test_selftest_and_cli_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(
        db_runtime,
        "validate_capabilities",
        lambda: {"safe_version": True, "fts5": True},
    )
    monkeypatch.setattr(
        package_selftest.importlib,
        "import_module",
        lambda _name: SimpleNamespace(__version__="1.0"),
    )
    results = iter(
        [
            SimpleNamespace(returncode=0, stdout="eng\nchi_sim\n"),
            SimpleNamespace(returncode=0, stdout=""),
        ]
    )
    calls: list[dict[str, object]] = []

    def fake_run(*_args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return next(results)

    monkeypatch.setattr(package_selftest.subprocess, "run", fake_run)
    result = package_selftest.run_selftest(runtime)
    assert result["passed"] is True
    assert result["frontend_assets"] == 1
    assert result["smart_runtime"] == {
        "numpy": "1.0",
        "onnxruntime": "1.0",
        "tokenizers": "1.0",
    }
    ocr_environment = calls[0]["env"]
    assert isinstance(ocr_environment, dict)
    assert ocr_environment["TESSDATA_PREFIX"] == str(runtime / "ocr" / "tessdata")
    assert ocr_environment["LD_LIBRARY_PATH"].split(os.pathsep)[0] == str(
        runtime / "ocr" / "lib"
    )

    monkeypatch.setattr(package_selftest, "run_selftest", lambda _runtime: result)
    assert package_selftest.main(runtime) == 0
    assert '"passed": true' in capsys.readouterr().out
    monkeypatch.setattr(
        package_selftest,
        "run_selftest",
        lambda _runtime: (_ for _ in ()).throw(RuntimeError("自检失败")),
    )
    assert package_selftest.main(runtime) == 2
    assert "自检失败" in capsys.readouterr().out
