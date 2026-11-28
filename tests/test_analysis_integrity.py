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

def test_fake_worker_recovers_hole_and_persists(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[Result "*"]\n\n1. e4 e5 2. Nf3 *'));store(g,p)
    enqueue(QueueRequest())
    w=Worker();job=w.claim()
    eid={"name":"fixture"}
    calls=[]
    class Engine:
        options={}
        def configure(self,*a):pass
        def quit(self):pass
    def payload(engine,b,m,s,identity):
        calls.append(m.uci())
        score={"cp":50,"mate":None,"pv":[m.uci()]}
        return {"engine":identity,"settings":s.model_dump(),"perspective":"white" if b.turn else "black","best":score,"played":score,"alternatives":[score]}
    monkeypatch.setattr("chessbuilder.engine.chess.engine.SimpleEngine.popen_uci",lambda *a,**kw:Engine())
    monkeypatch.setattr("chessbuilder.engine.identity",lambda *a:eid)
    monkeypatch.setattr("chessbuilder.engine.analyse_move",payload)
    with connect() as c:
        score={"cp":50,"mate":None,"pv":["g1f3"]}
        a={"engine":eid,"perspective":"white","best":score,"played":score,"alternatives":[score]}
        c.execute("INSERT INTO move_analysis VALUES(1,3,?)",(json.dumps(a),))
        c.execute("INSERT INTO move_analysis VALUES(1,2,'not-json')")
        c.execute("UPDATE jobs SET next_ply=4")
    w.process(job,"fixture")
    assert calls==["e2e4","e7e5"]
    with connect() as c:assert c.execute("SELECT status,next_ply FROM jobs").fetchone()[:]==("done",4)
    assert detail(1)["coverage"]=={"saved":3,"total":3,"status":"complete"}

def test_reimport_recovers_empty_duplicate(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[White "A"]\n[Black "B"]\n[Result "*"]\n\n*'));store(g,p,"chess.com","id")
    g,p=next(parse('[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 *'))
    assert not store(g,p,"chess.com","id")
    assert detail(1)["coverage"]["total"]==2

def test_library_status_uses_saved_evaluations(tmp_path,monkeypatch):
    from chessbuilder.review import games
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[Result "*"]\n\n1. e4 *'));store(g,p)
    enqueue(QueueRequest())
    with connect() as c:c.execute("UPDATE jobs SET status='done',next_ply=2")
    assert games(limit=50,offset=0)[0]["analysis_status"]=="unanalysed"
    assert not games(status="done",limit=50,offset=0)
