import chess
import chess.engine
from chessbuilder.db import migrate,connect
from chessbuilder.engine import Settings,analyse_move,enqueue,QueueRequest,control,Worker
from chessbuilder.importer import parse,store

class Fake:
    def __init__(self): self.calls=0
    def analyse(self,board,limit,multipv,root_moves):
        self.calls+=1
        moves=root_moves or list(board.legal_moves)[:multipv]
        return [{"score":chess.engine.PovScore(chess.engine.Cp(50-i*10),board.turn),"pv":[m],"depth":8,"nodes":100} for i,m in enumerate(moves)]

def test_cache_and_perspective(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    b=chess.Board();b.push_san("e4")
    engine=Fake();s=Settings();m=chess.Move.from_uci("e7e5")
    a=analyse_move(engine,b,m,s,{"name":"fixture"})
    assert a["perspective"]=="black" and a["played"]["cp"]==50
    calls=engine.calls
    assert analyse_move(engine,b,m,s,{"name":"fixture"})==a and engine.calls==calls
    analyse_move(engine,b,m,Settings(level="deep"),{"name":"fixture"})
    assert engine.calls>calls

def test_queue_transitions(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[Result "*"]\n\n1. e4 e5 *'));store(g,p)
    assert enqueue(QueueRequest())["queued"]==1
    assert enqueue(QueueRequest())["queued"]==0
    job=Worker().claim();assert job["next_ply"]==1
    assert control(job["id"],"pause")["status"]=="paused"
    assert Worker().claim() is None
    assert control(job["id"],"resume")["status"]=="queued"
    assert Worker().claim()["id"]==job["id"]

def test_critical_move_is_refined(tmp_path,monkeypatch):
    from chessbuilder.engine import LEVELS
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    class Tactical(Fake):
        def analyse(self,board,limit,multipv,root_moves):
            moves=root_moves or list(board.legal_moves)[:multipv]
            return [{"score":chess.engine.PovScore(chess.engine.Cp(-300 if root_moves else 300),board.turn),"pv":[m],"depth":8,"nodes":limit.nodes} for m in moves]
    b=chess.Board()
    m=chess.Move.from_uci("e2e4")
    result=analyse_move(Tactical(),b,m,Settings(),{"name":"fixture"})
    assert result["refined_from"]=="quick" and result["best"]["nodes"]==LEVELS["deep"]
    assert analyse_move(Tactical(),b,m,Settings(auto_deepen=False),{"name":"fixture"})["best"]["nodes"]==LEVELS["quick"]

def test_checkpoint_restarts_for_changed_engine(tmp_path,monkeypatch):
    import json
    from chessbuilder.engine import consistent_checkpoint
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[Result "*"]\n\n1. e4 e5 *'));store(g,p)
    enqueue(QueueRequest())
    job=Worker().claim()
    with connect() as c:
        c.execute("INSERT INTO move_analysis VALUES(?,?,?)",(job["id"],1,json.dumps({"engine":{"name":"old"},"perspective":"white","best":{"cp":50,"pv":["e2e4"]},"played":{"cp":50,"pv":["e2e4"]},"alternatives":[]})))
        c.execute("UPDATE jobs SET next_ply=2 WHERE id=?",(job["id"],))
    job["next_ply"]=2
    assert consistent_checkpoint(job,{"name":"old"})==2
    assert consistent_checkpoint(job,{"name":"new"})==1
    with connect() as c:
        assert c.execute("SELECT COUNT(*) FROM move_analysis").fetchone()[0]==0
        assert c.execute("SELECT next_ply FROM jobs").fetchone()[0]==1

def test_selective_batch_and_bulk_pause(tmp_path,monkeypatch):
    from chessbuilder.engine import preview,bulk_control,BulkRequest
    from chessbuilder.importer import account_id
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    aa=account_id("chess.com","Alice")["id"];bb=account_id("lichess","Bob")["id"]
    for i,(account,source,speed,day) in enumerate([(aa,"chess.com","rapid","2026-01-01"),(aa,"chess.com","rapid","2026-01-02"),(aa,"chess.com","rapid","2026-01-03"),(aa,"chess.com","blitz","2026-01-03"),(bb,"lichess","rapid","2026-01-02"),(aa,"chess.com","rapid","2025-12-31")]):
        g,p=next(parse('[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. e4 e5 *'))
        store(g,p,source,str(i),account,meta={"speed":speed,"played_at":day+"T23:59:59+00:00"})
    q=QueueRequest(account_id=aa,speed="rapid",start="2026-01-01",end="2026-01-03",limit=2)
    p=preview(q)
    assert (p["matched"],p["eligible"],p["selected"])==(3,3,2)
    assert [x["id"] for x in p["sample"]]==[3,2]
    with connect() as c:assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]==0
    assert enqueue(q)["queued"]==2
    assert enqueue(q)["queued"]==1
    assert enqueue(q)["queued"]==0
    job=Worker().claim()
    with connect() as c:c.execute("UPDATE jobs SET next_ply=2 WHERE id=?",(job["id"],))
    assert bulk_control(BulkRequest(action="pause",all_jobs=True))["changed"]==3
    r=enqueue(q.model_copy(update={"limit":1}))
    assert r["resumed"]==1 and r["queued"]==0
    with connect() as c:
        assert c.execute("SELECT next_ply,status FROM jobs WHERE id=?",(job["id"],)).fetchone()[:]==(2,"queued")
        assert c.execute("SELECT COUNT(*) FROM jobs WHERE status='paused'").fetchone()[0]==2
    assert bulk_control(BulkRequest(action="cancel",account_id=aa,speed="rapid"))["changed"]==3

def test_skip_completed_and_explicit_reanalysis(tmp_path,monkeypatch):
    from chessbuilder.engine import preview
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[Result "*"]\n\n1. e4 *'));store(g,p)
    enqueue(QueueRequest())
    with connect() as c:
        c.execute("UPDATE jobs SET status='done',next_ply=2")
        persist_fixture(c,1,1)
    assert preview(QueueRequest(level="deep"))["eligible"]==1
    assert preview(QueueRequest())["excluded"]=={"completed":1}
    assert enqueue(QueueRequest(game_id=1,level="deep"))["queued"]==1
    assert enqueue(QueueRequest(game_id=1,level="deep"))["queued"]==0

def test_job_pagination_and_account_filter(tmp_path,monkeypatch):
    from chessbuilder.engine import jobs,preview
    from chessbuilder.importer import account_id
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    a=account_id("chess.com","Alice")["id"]
    for i in range(4):
        g,p=next(parse('[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. e4 *'))
        store(g,p,"chess.com",str(i),a,"Alice")
    enqueue(QueueRequest(limit=3))
    assert jobs(status="queued",account_id=a,limit=2,offset=0)["total"]==3
    assert len(jobs(status="queued",account_id=a,limit=2,offset=2)["jobs"])==1
    assert jobs(status="done",limit=2,offset=0)["total"]==0

def test_depth_coverage_and_exclusion_reasons(tmp_path,monkeypatch):
    from chessbuilder.engine import preview,settings_json
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    for i in range(17):
        g,p=next(parse('[Result "*"]\n\n1. e4 e5 *'))
        store(g,p,"pgn",str(i),meta={"speed":"rapid","played_at":"2026-11-01T00:00:00+00:00"})
    with connect() as c:
        c.executemany("INSERT INTO jobs(game_id,settings,status,next_ply) VALUES(?,?,'done',3)",[(i,settings_json(Settings(level="quick"))) for i in range(1,18)])
        for i in range(1,18):persist_fixture(c,i,i)
        c.execute("INSERT INTO jobs(game_id,settings,status,next_ply) VALUES(1,?,'cancelled',2)",(settings_json(Settings(level="deep")),))
    q=QueueRequest(speed="rapid",start="2026-10-28",end="2026-11-28",level="deep")
    p=preview(q)
    assert (p["matched"],p["eligible"],p["resumable"],p["upgrades"])==(17,17,1,16)
    r=enqueue(q);assert (r["queued"],r["resumed"])==(16,1)
    assert preview(q)["excluded"]=={"active":17}
    with connect() as c:assert c.execute("SELECT next_ply FROM jobs WHERE game_id=1 AND status='queued'").fetchone()[0]==2

def test_deeper_completed_and_multipv_coverage(tmp_path,monkeypatch):
    from chessbuilder.engine import preview,settings_json
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[Result "*"]\n\n1. e4 *'));store(g,p)
    with connect() as c:
        c.execute("INSERT INTO jobs(game_id,settings,status,next_ply) VALUES(1,?,'done',2)",(settings_json(Settings(level="deep",multipv=2)),))
        persist_fixture(c,1,1)
    assert preview(QueueRequest(level="quick"))["excluded"]=={"completed":1}
    assert preview(QueueRequest(level="deep",multipv=3))["eligible"]==1
    assert preview(QueueRequest(level="detailed"))["eligible"]==1
    assert preview(QueueRequest(level="deep",skip_completed=False))["excluded"]=={"completed":1}


def persist_fixture(c,job_id,game_id):
    import json
    for row in c.execute("SELECT ply,uci FROM moves WHERE game_id=? ORDER BY ply",(game_id,)).fetchall():
        score={"cp":50,"mate":None,"pv":[row["uci"]]}
        a={"perspective":"white" if row["ply"]%2 else "black","best":score,"played":score,"alternatives":[score],"engine":{"name":"fixture"}}
        c.execute("INSERT OR REPLACE INTO move_analysis VALUES(?,?,?)",(job_id,row["ply"],json.dumps(a)))
