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

def test_transfer_excludes_source_games_and_same_day(local):
    from chessbuilder.coach import competitive_transfer
    with connect() as c:
        insert_item(c,"lichess",None,None,None,chess.Board(),"calculation",["fork"],[["e2e4"]],{},1500,"transfer")
        c.execute("INSERT INTO training_attempts(item_id,started_at,finished_at,correct,status) VALUES(1,'2024-02-01T10:00:00+00:00','2024-02-01T10:01:00+00:00',1,'passed')")
        def observation(gid,day,kind,speed="rapid"):
            return {"game_id":gid,"date":day,"phase":"middlegame","speed":speed,"findings":[{"theme":"fork","kind":kind}]}
        result=competitive_transfer(c,[observation(1,"2024-01-20","missed"),observation(2,"2024-02-01","missed"),observation(3,"2024-02-10","exploited"),observation(4,"2024-02-10","missed","blitz")])
    assert len(result)==1 and result[0]["before_n"]==1 and result[0]["after_n"]==1
    assert result[0]["before_rate"]==1 and result[0]["after_rate"]==0 and result[0]["confidence"]=="limited"

def test_plan_does_not_repeat_same_position(local):
    with connect() as c:
        for key in ("a","b"):
            insert_item(c,"lichess",None,None,None,chess.Board(),"calculation",["fork"],[["e2e4"]],{},1500,key)
    assert len(create_session(PlanRequest(minutes=20))["items"])==1

def test_adaptive_separates_novel_repeat_and_sources(local):
    from chessbuilder.adaptive import practice_profile,practice_signal
    from datetime import date
    with connect() as c:
        b=chess.Board()
        for i in range(8):
            pos=chess.Board()
            pos.push(list(pos.legal_moves)[i])
            move=next(iter(pos.legal_moves)).uci()
            insert_item(c,"lichess",None,None,None,pos,"calculation",["fork"],[[move]],{},1500,"adaptive-"+str(i))
            c.execute("INSERT INTO training_attempts(item_id,started_at,finished_at,correct,hints,status) VALUES(?,?,?,?,?,?)",(i+1,"2026-01-01T10:00:00+00:00","2026-01-01T10:01:00+00:00",1,0 if i<3 else 1,"passed"))
        c.execute("INSERT INTO training_attempts(item_id,started_at,finished_at,correct,hints,status) VALUES(1,'2026-01-02T10:00:00+00:00','2026-01-02T10:01:00+00:00',1,0,'passed')")
        p=practice_profile(c,date(2026,1,2))
        assert len(p)==1 and p[0]["novel"]==8 and p[0]["unassisted"]==3 and p[0]["repeat"]==1
        assert p[0]["target_difficulty"]==1400
        r=c.execute("SELECT * FROM training_items WHERE id=1").fetchone()
        score,reasons=practice_signal(r,p)
        assert score>0 and any("1400" in x for x in reasons)
        assert practice_profile(c,date(2025,12,31))==[]

def test_adaptive_plan_excludes_already_seen_duplicate(local):
    from chessbuilder.coach import select_plan
    from datetime import date
    with connect() as c:
        for key in ("first","duplicate"):
            insert_item(c,"lichess",None,None,None,chess.Board(),"calculation",["fork"],[["e2e4"]],{},1500,key)
        c.execute("INSERT INTO training_attempts(item_id,started_at,finished_at,correct,hints,status) VALUES(1,'2026-01-02T10:00:00+00:00','2026-01-02T10:01:00+00:00',1,0,'passed')")
        plan=select_plan(c,PlanRequest(minutes=20),date(2026,1,3))
        assert all(x["item_id"]!=2 for x in plan["items"])
        assert plan["items"][0]["item_id"]==1 and plan["items"][0]["reason"]=="review_due"
