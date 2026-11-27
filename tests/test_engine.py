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
        c.execute("INSERT INTO move_analysis VALUES(?,?,?)",(job["id"],1,json.dumps({"engine":{"name":"old"}})))
        c.execute("UPDATE jobs SET next_ply=2 WHERE id=?",(job["id"],))
    job["next_ply"]=2
    assert consistent_checkpoint(job,{"name":"old"})==2
    assert consistent_checkpoint(job,{"name":"new"})==1
    with connect() as c:
        assert c.execute("SELECT COUNT(*) FROM move_analysis").fetchone()[0]==0
        assert c.execute("SELECT next_ply FROM jobs").fetchone()[0]==1
