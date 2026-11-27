from datetime import datetime,timezone
import chess
import pytest
from fastapi import HTTPException
from chessbuilder.db import migrate,connect
from chessbuilder.training import insert_item,start_attempt,answer,Answer
from chessbuilder.repetition import next_review,create_collection,Collection,new_cycle,Cycle,start,complete,due,cycles

def test_deterministic_schedule():
    when=datetime(2024,1,1,tzinfo=timezone.utc)
    good=next_review(None,1,0,1000,when,"lichess")
    bad=next_review(None,0,0,1000,when,"lichess")
    assert good["due_at"]=="2024-01-02T00:00:00+00:00" and bad["due_at"]=="2024-01-01T06:00:00+00:00"
    assert next_review(good,1,0,1000,when,"lichess")==next_review(good,1,0,1000,when,"lichess")

def test_fixed_cycle_and_resume(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    with connect() as c:
        for i in range(2):insert_item(c,"lichess",None,None,None,chess.Board(),"calculation",["fork"],[["e2e4"]],{},1500,str(i))
    cid=create_collection(Collection(name="test",item_ids=[2,1]))["id"]
    s=new_cycle(cid,Cycle(target_minutes=10));assert [r["item_id"] for r in s["items"]]==[2,1]
    assert new_cycle(cid,Cycle(target_minutes=1))["id"]==s["id"]
    with pytest.raises(HTTPException):complete(s["id"])
    for item in [2,1]:
        a=start(s["id"],item);assert start(s["id"],item)["id"]==a["id"]
        assert start_attempt(item)["id"]!=a["id"]
        answer(a["id"],Answer(uci="e2e4",expected_ply=0))
    done=complete(s["id"]);assert done["metrics"]["accuracy"]==1
    assert new_cycle(cid,Cycle(target_minutes=5))["number"]==2
    assert len(cycles(cid))==2
    assert not due(limit=100)
