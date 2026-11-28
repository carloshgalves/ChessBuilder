import chess
import pytest
from fastapi import HTTPException
from chessbuilder.db import migrate,connect
from chessbuilder.importer import parse,store
from chessbuilder.explore import Position,Evaluate,position,evaluate

def fixture(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[Result "*"]\n\n1. e4 e5 2. Nf3 *'));store(g,p)

def test_legal_branches_without_mutation(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    a=position(Position(game_id=1,ply=1,moves=["c7c5","g1f3"]))
    b=position(Position(game_id=1,ply=1,moves=["e7e5"]))
    assert a["line"][0]["san"]=="c5" and b["line"][0]["san"]=="e5"
    assert a["fen"]!=b["fen"] and a["root_fen"]==b["root_fen"]
    with pytest.raises(HTTPException):position(Position(game_id=1,ply=1,moves=["e2e4"]))
    with pytest.raises(HTTPException):position(Position(game_id=1,ply=4))
    with connect() as c:
        assert c.execute("SELECT COUNT(*) FROM moves").fetchone()[0]==3
        assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM move_analysis").fetchone()[0]==0

def test_repetition_history_preserved(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    p=position(Position(game_id=1,ply=0,moves=["g1f3","g8f6","f3g1","f6g8"]*2))
    assert p["can_claim_draw"] and p["result"] is None

def test_evaluation_is_bounded_and_read_only(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    from chessbuilder.engine import executable
    if not executable():pytest.skip("Stockfish unavailable")
    p=evaluate(Evaluate(game_id=1,ply=1,multipv=3))
    assert 1<=len(p["candidates"])<=3 and p["engine"]
    assert all(x["pv"][0] in p["legal_moves"] for x in p["candidates"])
    with connect() as c:
        assert c.execute("SELECT COUNT(*) FROM move_analysis").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]==0

def test_busy_engine_rejects_interactive_request(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    from chessbuilder.engine import ENGINE_SEARCH_LOCK
    monkeypatch.setattr("chessbuilder.explore.executable",lambda:"fixture")
    with ENGINE_SEARCH_LOCK:
        with pytest.raises(HTTPException) as ex:evaluate(Evaluate(game_id=1,ply=0))
        assert ex.value.status_code==409
