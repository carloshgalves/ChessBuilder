from chessbuilder.dashboard import dashboard,trajectory
from chessbuilder.db import migrate
from chessbuilder.importer import parse,store

def game(result,date,rating,opponent):
    return f'[White "Alice"]\n[Black "Bob"]\n[WhiteElo "{rating}"]\n[BlackElo "{opponent}"]\n[Date "{date}"]\n[Result "{result}"]\n[TimeControl "900+10"]\n\n1. e4 e5 {result}\n'

def test_history(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    for r,d,a,b in [("1-0","2024.01.01",1700,1850),("0-1","2024.02.01",1750,1600)]:
        g,p=next(parse(game(r,d,a,b)));store(g,p,username="Alice")
    s=dashboard(speed="rapid")
    assert s["total"]==2 and s["outcomes"]=={"win":1,"loss":1,"draw":0}
    assert s["ratings"][0]["delta"]==50 and s["gaps"][0]["days"]==31
    assert s["accuracy"]["all"] is None
    assert s["best_win"]["rating"]==1850
    assert dashboard(speed="blitz")["total"]==0
    assert dashboard(start="2024-02-01",end="2024-02-29")["previous"]["total"]==0

def test_unowned_games_and_zero_loss(tmp_path,monkeypatch):
    import json
    from chessbuilder.db import connect
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse(game("1-0","2024.01.01",1700,1800)));store(g,p,username="Alice")
    g,p=next(parse(game("0-1","2024.02.01",1800,1900)));store(g,p)
    with connect() as c:
        c.execute("INSERT INTO jobs(game_id,settings,status,next_ply) VALUES(1,'{}','done',3)")
        score={"cp":50,"mate":None,"pv":["e2e4"]}
        a={"perspective":"white","best":score,"played":score,"alternatives":[score]}
        c.execute("INSERT INTO move_analysis VALUES(1,1,?)",(json.dumps(a),))
    s=dashboard()
    assert s["total"]==2 and s["personal_games"]==1 and s["completed_personal_games"]==1
    assert not s["gaps"] and sum(x["games"] for x in s["months"])==1
    assert s["accuracy"]["all"]==100 and s["worst_move"] is None

def test_historical_games_excluded_from_default_dashboard(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    g,p=next(parse(game("1-0","2024.01.01",1700,1800)));store(g,p,username="Alice")
    store(g,p,"historical","champion-fixture")
    assert dashboard()["total"]==1
    assert dashboard(source="historical")["total"]==1
