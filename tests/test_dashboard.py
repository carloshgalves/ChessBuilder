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
