import pytest
from fastapi import HTTPException
from chessbuilder.db import migrate,connect
from chessbuilder.books import Import,import_material,candidates,Review,review,export_fen
from chessbuilder.training import start_attempt,answer,Answer

def test_book_review_gate(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    fen="7k/5Q2/6K1/8/8/8/8/8 w - - 0 1"
    mid=import_material(Import(title="Original",provenance="fixture",format="fen",content=fen))["id"]
    cid=candidates(mid)[0]["id"]
    with pytest.raises(HTTPException):review(cid,Review(fen="8/8/8/8/8/8/8/8 w - - 0 1",author_lines=["Qg7"],mode="author",approved=True))
    r=review(cid,Review(fen=fen,author_lines=["Qg7#"],mode="author"))
    assert not r["approved"]
    with connect() as c:assert c.execute("SELECT COUNT(*) FROM training_items").fetchone()[0]==0
    r=review(cid,Review(fen=fen,author_lines=["Qg7#"],mode="author",approved=True))
    assert export_fen(cid)==fen
    a=start_attempt(r["item_id"])
    assert answer(a["id"],Answer(uci="f7g7",expected_ply=0))["correct"]==1
