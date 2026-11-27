"""Integration against a real UCI executable when installed."""
import json
import pytest
from chessbuilder.db import migrate,connect
from chessbuilder.importer import parse,store
from chessbuilder.engine import Worker,QueueRequest,enqueue,executable
from chessbuilder.review import detail
from chessbuilder.dashboard import dashboard

@pytest.mark.skipif(not executable(),reason="Stockfish executable unavailable")
def test_real_stockfish_pipeline(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path))
    migrate()
    g,p=next(parse('[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0'))
    assert store(g,p,username="Alice")
    enqueue(QueueRequest(threads=1,hash_mb=16,multipv=2))
    w=Worker()
    job=w.claim()
    w.process(job,executable())
    with connect() as c:
        assert c.execute("SELECT status FROM jobs").fetchone()[0]=="done"
        assert c.execute("SELECT COUNT(*) FROM move_analysis").fetchone()[0]==4
        count=c.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
        assert count>0
    result=detail(1)
    assert result["metrics"]["white"]["analysed"]==2
    assert result["metrics"]["black"]["analysed"]==2
    assert all(m["best_line"] for m in result["moves"])
    assert dashboard()["analysed_plies"]==2
    with connect() as c:
        c.execute("UPDATE jobs SET status='queued',next_ply=1")
    w.process(w.claim(),executable())
    with connect() as c:
        assert c.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]==count
