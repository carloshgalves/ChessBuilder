import json
import chess
import pytest
from fastapi import HTTPException
from chessbuilder.db import migrate,connect
from chessbuilder.importer import parse,store
from chessbuilder.training import generate,Generate,items,start_attempt,answer,Answer,hint,reveal,import_puzzles,PuzzleImport,equivalent

@pytest.fixture
def local(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()

def test_personal_exercise(local):
    pgn='[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. a3 *'
    g,p=next(parse(pgn));store(g,p,username="Alice")
    a={"perspective":"white","settings":{"level":"deep"},"best":{"cp":200,"mate":None,"depth":15,"pv":["e2e4","e7e5"]},"played":{"cp":-100,"mate":None,"depth":15,"pv":["a2a3","e7e5"]},"alternatives":[{"cp":190,"mate":None,"depth":15,"pv":["d2d4","d7d5"]}]}
    with connect() as c:
        c.execute("INSERT INTO jobs(game_id,settings,status,next_ply) VALUES(1,'{}','done',2)")
        c.execute("INSERT INTO move_analysis VALUES(1,1,?)",(json.dumps(a),))
    assert generate(Generate())["added"]==1
    assert generate(Generate())["added"]==0
    item=items(limit=50,offset=0)[0]
    assert "solution" not in item
    attempt=start_attempt(item["id"]);assert "solutions" not in attempt
    assert start_attempt(item["id"])["id"]==attempt["id"]
    with pytest.raises(HTTPException):answer(attempt["id"],Answer(uci="e2e5",expected_ply=0))
    assert hint(attempt["id"])["hints"]==1
    end=answer(attempt["id"],Answer(uci="d2d4",expected_ply=0))
    assert end["correct"]==1 and end["hints"]==1 and end["solutions"]
    with pytest.raises(HTTPException):answer(attempt["id"],Answer(uci="e2e4",expected_ply=0))
    assert reveal(start_attempt(item["id"])["id"])["status"]=="abandoned"

def test_lichess_multi_move_and_dedup(local):
    csv="PuzzleId,FEN,Moves,Rating,Themes\nabc,"+chess.STARTING_FEN+",e2e4 e7e5 g1f3 b8c6,1500,fork\n"
    assert import_puzzles(PuzzleImport(csv_text=csv))["added"]==1
    assert import_puzzles(PuzzleImport(csv_text=csv))["skipped"]==1
    item=items(limit=50,offset=0)[0];a=start_attempt(item["id"])
    assert item["turn"]=="black"
    a=answer(a["id"],Answer(uci="e7e5",expected_ply=0))
    assert a["status"]=="active" and a["answers"]==["e7e5","g1f3"]
    a=answer(a["id"],Answer(uci="b8c6",expected_ply=2))
    assert a["status"]=="passed"

def test_mate_equivalence():
    assert equivalent({"mate":2},{"mate":3})
    assert not equivalent({"mate":2},{"cp":900,"mate":None})
    assert not equivalent({"mate":2},{"mate":-2})
