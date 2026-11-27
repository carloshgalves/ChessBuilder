import chess
import pytest
from chessbuilder.db import migrate
from chessbuilder.endgames import CATALOG,catalog_board,prepare,Start,exact_value
from chessbuilder.training import start_attempt,answer,Answer

def test_catalog_legal_variants():
    for entry in CATALOG:
        for v in range(4):
            b=catalog_board(entry,v)
            assert b.is_valid() and not b.is_game_over()

def test_endgame_mate_persists(tmp_path,monkeypatch):
    from chessbuilder.db import connect
    from chessbuilder.training import insert_item
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    b=chess.Board("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    with connect() as c:
        insert_item(c,"endgame",None,None,None,b,"endgame_win",["queen"],[],{"goal":"win","theory":"Mate","remote":False},1,"fixture")
    a=start_attempt(1)
    a=answer(a["id"],Answer(uci="f7g7",expected_ply=0))
    assert a["status"]=="passed" and a["result"]=="1-0"
    assert exact_value(chess.Board(a["fen"]))["source"]=="rules"

def test_prepare_without_tablebase(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));monkeypatch.delenv("CHESSBUILDER_SYZYGY_DIR",raising=False);migrate()
    r=prepare(Start(key="queen"))
    assert r["evaluation"] is None and r["objective"]=="win"
    assert prepare(Start(key="queen"))["item_id"]==r["item_id"]

def test_recognition_only_grades_exact(tmp_path,monkeypatch):
    from chessbuilder.endgames import recognize,Recognition
    from chessbuilder.db import connect
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    monkeypatch.setattr("chessbuilder.endgames.exact_value",lambda *args:None)
    assert not recognize(Recognition(key="queen",guess=1))["graded"]
    monkeypatch.setattr("chessbuilder.endgames.exact_value",lambda *args:{"effective":1,"wdl":2,"dtz":1,"source":"fixture"})
    assert recognize(Recognition(key="queen",guess=1))["correct"]
    with connect() as c:assert c.execute("SELECT COUNT(*) FROM training_attempts WHERE correct=1").fetchone()[0]==1

def test_opposite_colored_bishops():
    e=next(x for x in CATALOG if x[0]=="bishops")
    b=catalog_board(e,0)
    squares=list(b.pieces(chess.BISHOP,chess.WHITE))+list(b.pieces(chess.BISHOP,chess.BLACK))
    assert bool(chess.BB_SQUARES[squares[0]] & chess.BB_LIGHT_SQUARES)!=bool(chess.BB_SQUARES[squares[1]] & chess.BB_LIGHT_SQUARES)
