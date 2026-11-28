import json
import chess
from chessbuilder.db import migrate,connect
from chessbuilder.importer import parse,store,restore_empty_games
from chessbuilder.engine import Worker,enqueue,QueueRequest,preview
from chessbuilder.integrity import reconcile_jobs,first_missing
from chessbuilder.review import detail
from chessbuilder.engine import Settings,settings_json

def test_empty_pgn_is_not_analysis(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[SetUp "1"]\n[FEN "4k3/8/8/8/8/8/8/4K3 w - - 0 1"]\n[Result "1/2-1/2"]\n\n1/2-1/2'));store(g,p)
    with connect() as c:
        c.execute("INSERT INTO jobs(game_id,settings,status) VALUES(1,?,'done')",(settings_json(Settings()),))
        assert reconcile_jobs(c)==1
        assert c.execute("SELECT status FROM jobs").fetchone()[0]=="failed"
    assert restore_empty_games()==0
    assert preview(QueueRequest(game_id=1))["excluded"]=={"no_moves":1}
    assert detail(1)["coverage"]=={"saved":0,"total":0,"status":"no_moves"}

def test_recovery_and_saved_progress(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[Result "*"]\n\n1. e4 e5 2. Nf3 *'));store(g,p)
    with connect() as c:c.execute("DELETE FROM moves WHERE game_id=1")
    assert restore_empty_games()==1 and restore_empty_games()==0
    enqueue(QueueRequest())
    with connect() as c:
        c.execute("UPDATE jobs SET status='done',next_ply=4")
        score={"cp":30,"mate":None,"pv":["e2e4"]}
        payload={"perspective":"white","best":score,"played":score,"alternatives":[score],"engine":{"name":"fixture"}}
        c.execute("INSERT INTO move_analysis VALUES(1,1,?)",(json.dumps(payload),))
        assert reconcile_jobs(c)==1
        assert first_missing(c,1,1)==(2,1,3)
        assert c.execute("SELECT status FROM jobs").fetchone()[0]=="paused"
    d=detail(1)
    assert d["coverage"]=={"saved":1,"total":3,"status":"partial"} and d["moves"][0]["analysis"]
    assert enqueue(QueueRequest())["resumed"]==1

def test_worker_saves_and_reopens_after_restart(tmp_path,monkeypatch):
    from chessbuilder.engine import executable
    import pytest
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    path=executable()
    if not path:pytest.skip("Stockfish unavailable")
    g,p=next(parse('[Result "*"]\n\n1. e4 e5 2. Nf3 *'));store(g,p)
    enqueue(QueueRequest(threads=1,hash_mb=16,auto_deepen=False))
    w=Worker();w.process(w.claim(),path)
    # Reopen SQLite using new connections, not worker memory.
    migrate()
    d=detail(1)
    assert d["coverage"]=={"saved":3,"total":3,"status":"complete"}
    assert d["metrics"]["white"]["analysed"]==2 and d["metrics"]["black"]["analysed"]==1
    with connect() as c:assert reconcile_jobs(c)==0
