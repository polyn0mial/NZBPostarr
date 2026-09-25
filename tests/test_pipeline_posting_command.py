from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from logic.pipeline import posting, runner


def test_nyuu_command_line_is_pinned(monkeypatch) -> None:
    # nyuu is an external tool: this is the exact argv the posting step hands it.
    monkeypatch.setattr(posting.random, "sample", lambda seq, k: list(seq)[:k])
    conf = SimpleNamespace(
        nyuu_path="/usr/local/bin/nyuu",
        article_size="700K",
        poster="Poster <poster@example.com>",
        alt_bins=["alt.binaries.a", "alt.binaries.b", "alt.binaries.c", "alt.binaries.d"],
    )
    server = SimpleNamespace(host="news.example.com", port=563, user="poster", password="secret", ssl=True)
    nzb = Path("out") / "Release.nzb"
    inputs = [Path("work") / "Release.part01.rar", Path("work") / "Release.par2"]

    command = posting._build_nyuu_command(conf, server, nzb, inputs, 20)

    assert command == [
        "/usr/local/bin/nyuu",
        "-h", "news.example.com",
        "-P", "563",
        "-u", "poster",
        "-p", "secret",
        "-n", "20",
        "--connection-threads", "4",
        "--check-connections", "5",
        "--check-tries", "10",
        "-S",
        "-a", "700K",
        "-f", "Poster <poster@example.com>",
        "-o", str(nzb),
        "-O",
        "--nzb-cork",
        "--use-post-pool",
        "--disk-req-size", "1M",
        "--progress", "log:2s",
        str(inputs[0]),
        str(inputs[1]),
        "-g", "alt.binaries.a,alt.binaries.b,alt.binaries.c",
    ]


@pytest.mark.parametrize(
    ("result", "success", "duplicate", "failure", "completed", "consecutive"),
    [
        (0, 1, 0, 0, 1, 0),
        (1, 0, 1, 0, 1, 2),
        (2, 0, 0, 0, 0, 2),
        (3, 0, 0, 1, 1, 3),
        (4, 0, 0, 1, 1, 3),
    ],
)
def test_item_return_codes_update_run_counts(
    monkeypatch, result, success, duplicate, failure, completed, consecutive
) -> None:
    # DECISIONS: return code 4 is distinct from 3, but both count a consecutive failure.
    monkeypatch.setattr(runner, "get_thread_job", lambda: None)
    monkeypatch.setattr(runner, "update_job_progress", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "log_completed", lambda *_args, **_kwargs: None)
    state = runner._JobRunState(total=5, effective_limit=None, test_mode=False, consecutive_failures=2)
    future: Future[int] = Future()
    future.set_result(result)
    state.active_upload_future = future
    state.active_upload_validation = SimpleNamespace(path=Path("Release"))

    state.complete_active_upload(wait=True)

    assert (state.success_count, state.duplicate_count, state.failure_count) == (success, duplicate, failure)
    assert state.completed_count == completed
    assert state.consecutive_failures == consecutive
    assert state.stop_processing is (result == 2)
