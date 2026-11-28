from chessbuilder.review import metrics,summarize,line,detail
from chessbuilder.db import migrate,connect
from chessbuilder.importer import parse,store
import chess

def test_accuracy():
    a={"perspective":"white","best":{"cp":200,"mate":None},"played":{"cp":0,"mate":None}}
    m=metrics(a)
    assert m["cp_loss"]==200 and m["accuracy"]<70 and m["win_loss"]>10
    assert summarize([a])["black"]["accuracy"] is None
    a["played"]=a["best"]
    assert metrics(a)["accuracy"]==100
    a["played"]={"cp":None,"mate":-2}
    assert metrics(a)["cp_loss"] is None

def test_line_and_detail(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[White "Alice"]\n[Black "Bob"]\n\n1. e4 e5 *'));store(g,p)
    d=detail(1)
    assert d["moves"][0]["san"]=="e4" and d["metrics"]["white"]["accuracy"] is None
    assert line(chess.Board(),["e2e4","e7e5"])[1]["san"]=="e5"

def test_accuracy_boundaries_and_wdl():
    from chessbuilder.review import win_percent
    for color in ("white","black"):
        a={"perspective":color,"best":{"cp":0,"mate":None},"played":{"cp":1,"mate":None}}
        assert metrics(a)["accuracy"]==100
        assert metrics(a)["expected_score_loss"] is None
        a["best"]["wdl"]=[500,400,100]
        a["played"]["wdl"]=[300,400,300]
        assert abs(metrics(a)["expected_score_loss"]-0.2)<1e-9
        assert summarize([a])[color]["expected_score_loss"]==0.2
    assert win_percent({"mate":2})==100
    assert win_percent({"mate":-2})==0
    assert win_percent({"cp":None,"mate":None}) is None

def test_review_prefers_deeper_complete_job(tmp_path,monkeypatch):
    import json
    from chessbuilder.review import select_job,filters
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse('[Result "*"]\n\n1. e4 e5 *'));store(g,p)
    with connect() as c:
        for level,status,next_ply in [("deep","done",3),("quick","done",3),("detailed","running",2)]:
            jid=c.execute("INSERT INTO jobs(game_id,settings,status,next_ply) VALUES(1,?,?,?)",(json.dumps({"level":level}),status,next_ply)).lastrowid
            for i,uci in enumerate(["e2e4","e7e5"][:next_ply-1],1):
                score={"cp":50,"mate":None,"pv":[uci]}
                c.execute("INSERT INTO move_analysis VALUES(?,?,?)",(jid,i,json.dumps({"perspective":"white" if i==1 else "black","best":score,"played":score,"alternatives":[score]})))
        assert json.loads(select_job(c,1)["settings"])["level"]=="deep"
        where,args=filters(status="running")
        assert not c.execute("SELECT * FROM games g"+where,args).fetchall()

def test_invalid_pv_is_truncated():
    assert len(line(chess.Board(),["e2e4","invalid"]))==1
