from chessbuilder.time_management import time_management,summarize_time
from chessbuilder.db import migrate,connect
from chessbuilder.importer import parse,store,account_id
import json

def test_time_report_uses_prior_clock_and_personal_seat(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    a=account_id("chess.com","Alice")["id"]
    raw='[White "Alice"]\n[Black "Bob"]\n[TimeControl "60+5"]\n\n1. e4 {[%clk 0:00:25]} e5 {[%clk 0:00:55]} 2. Nf3 {[%clk 0:00:20]} Nc6 *'
    g,p=next(parse(raw));store(g,p,"chess.com","1",a,"Alice",{"speed":"bullet"})
    with connect() as c:
        c.execute("INSERT INTO jobs(game_id,settings,status,next_ply) VALUES(1,'{}','paused',4)")
        best={"cp":200,"mate":None,"pv":["g1f3"]}
        played={"cp":0,"mate":None,"pv":["g1f3"]}
        c.execute("INSERT INTO move_analysis VALUES(1,3,?)",(json.dumps({"perspective":"white","best":best,"played":played,"alternatives":[best]}),))
    r=time_management(account_id=a,limit=25)
    g=r["groups"][0]
    assert r["sample_games"]==1 and g["moves"]==2 and g["timed"]==1 and g["median_seconds"]==10
    assert g["clock_before_known"]==1 and g["buckets"][0]["critical"]==1 and g["buckets"][0]["analysed"]==1
    assert g["buckets"][1]["critical_rate"] is None
    assert time_management(speed="rapid",limit=25)["groups"]==[]

def test_correspondence_clock_not_used_as_pre_move_budget(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[White "Alice"]\n[Black "Bob"]\n[TimeControl "86400"]\n\n1. e4 {[%clk 23:00:00]} e5 2. Nf3 {[%clk 22:00:00]} *'))
    store(g,p,"chess.com","daily",username="Alice",meta={"speed":"daily"})
    r=time_management(limit=25)["groups"][0]
    assert r["timed"]==0 and r["clock_before_known"]==0
