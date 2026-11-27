import pytest
from chessbuilder.db import migrate,connect
from chessbuilder.importer import parse,store
from chessbuilder.engine import Worker,QueueRequest,enqueue,executable

@pytest.mark.skipif(not executable(),reason="Stockfish executable unavailable")
def test_real_uci_and_cache(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. e4 e5 *'))
    store(g,p,username="Alice")
    enqueue(QueueRequest(threads=1,hash_mb=16,multipv=2))
    worker=Worker()
    worker.process(worker.claim(),executable())
    with connect() as c:
        assert c.execute("SELECT status FROM jobs").fetchone()[0]=="done"
        assert c.execute("SELECT COUNT(*) FROM move_analysis").fetchone()[0]==2
        count=c.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
        assert count>0
        c.execute("UPDATE jobs SET status='queued',next_ply=1")
    worker.process(worker.claim(),executable())
    with connect() as c:
        assert c.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]==count
