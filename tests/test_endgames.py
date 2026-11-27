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
