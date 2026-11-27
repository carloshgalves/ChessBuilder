import json
import chess
import pytest
from fastapi import HTTPException
from chessbuilder.db import connect,migrate
from chessbuilder.coach import aggregate,wilson,create_session,PlanRequest,start_session_item,complete_session,adherence,report
from chessbuilder.training import insert_item,answer,Answer

@pytest.fixture
def local(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()

def test_aggregate_uses_opportunities_not_labels():
    f={"theme":"fork","kind":"missed","confidence":"line_witness"}
    row={"game_id":1,"ply":3,"phase":"middlegame","speed":"rapid","loss":20,"expected_loss":0.2,"findings":[f,f]}
    result=aggregate([row])[0]
    assert result["opportunities"]==1 and result["failures"]==1
    assert result["confidence"]=="limited" and result["distinct_error_games"]==1
    assert wilson(1,1)[0]<0.25
    assert result["expected_score_loss"]==0.2

def test_plan_persistence_and_completion(local):
    with connect() as c:
        b=chess.Board()
        insert_item(c,"lichess",None,None,None,b,"calculation",["fork"],[["e2e4"]],{},1500,"fixture")
    body=PlanRequest(minutes=5,speed="rapid")
    s=create_session(body)
    assert s["plan"]["allocated"]==4 and s["plan"]["unallocated"]==1
    assert create_session(body)["id"]==s["id"]
    with pytest.raises(HTTPException):complete_session(s["id"])
    aid=start_session_item(s["id"],s["items"][0]["item_id"])["id"]
    assert start_session_item(s["id"],s["items"][0]["item_id"])["id"]==aid
    assert answer(aid,Answer(uci="e2e4",expected_ply=0))["status"]=="passed"
    assert complete_session(s["id"])["completed_at"]
    assert adherence()["completed"]==1
    r=report(days=30,limit=100)
    assert r["practice"]["novel"]["rate"]==1
    assert r["practice"]["repeat"]["rate"] is None

def test_empty_plan_can_be_refreshed_and_session_isolated(local):
    from chessbuilder.training import start_attempt
    req=PlanRequest(minutes=5)
    empty=create_session(req)
    assert empty["items"]==[]
    with connect() as c:
        insert_item(c,"lichess",None,None,None,chess.Board(),"calculation",["fork"],[["e2e4"]],{},1500,"later")
    s=create_session(req)
    assert s["id"]==empty["id"] and len(s["items"])==1
    item=s["items"][0]["item_id"]
    a=start_session_item(s["id"],item)
    standalone=start_attempt(item)
    assert a["id"]!=standalone["id"]

def test_latest_mode_uses_disjoint_game_windows(local):
    from chessbuilder.importer import parse,store
    for day in ("2024.01.01","2024.01.02","2024.01.03"):
        g,p=next(parse(f'[White "Alice"]\n[Black "Bob"]\n[Date "{day}"]\n[Result "*"]\n\n1. e4 *'))
        store(g,p,username="Alice")
    r=report(days=30,limit=1,end="2024-06-01",mode="latest")
    assert r["coverage"]["personal_games"]==1 and r["previous_coverage"]["personal_games"]==1
