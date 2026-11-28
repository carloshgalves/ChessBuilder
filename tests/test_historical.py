from chessbuilder.db import migrate,connect
from chessbuilder.historical import Corpus,import_corpus,Curate,curate,catalog
from chessbuilder.training import start_attempt,answer,Answer

def test_historical_import_and_curation(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    body=Corpus(edition="fixture",provenance="original",expected_games=2,pgn='[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 *')
    assert import_corpus(body)["added"]==1
    assert import_corpus(body)["duplicates"]==1
    assert catalog()["corpora"][0]["missing"]==1
    r=curate(Curate(game_id=1,ply=2))
    a=start_attempt(r["item_id"])
    assert answer(a["id"],Answer(uci="e7e5",expected_ply=0))["correct"]==1
    with connect() as c:assert c.execute("SELECT player_color FROM games").fetchone()[0] is None
