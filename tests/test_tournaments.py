from datetime import date,timedelta
from chessbuilder.db import migrate
from chessbuilder.tournaments import Tournament,create,detail,record,Round,speed_for

def test_tournament_records(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    day=date(2025,1,1)
    t=create(Tournament(name="Open",player="Alice",start_date=day,end_date=day+timedelta(days=1),rounds=7))
    d=record(t["id"],1,Round(opponent="Bob",color="white",result="win",opponent_rating=1700,pgn='[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n\n1. e4 1-0'))
    assert d["summary"]["points"]==1 and d["results"][0]["game_id"]
    assert detail(t["id"])["summary"]["average_opponent_rating"]==1700
    assert speed_for(15,10)=="rapid"
