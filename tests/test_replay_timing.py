import chess
from chessbuilder.db import migrate
from chessbuilder.importer import parse,store
from chessbuilder.review import detail

def test_replay_timing_from_existing_pgn(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    raw='[White "A"]\n[Black "B"]\n[WhiteElo "1700"]\n[BlackElo "1800"]\n[TimeControl "300+5"]\n\n1. e4 {[%clk 0:04:58] [%emt 0:00:07]} e5 {[%clk 0:05:02]} 2. Nf3 {[%clk 0:04:53]} Nc6 {[%clk 0:04:59]} *'
    g,p=next(parse(raw));store(g,p)
    d=detail(1);m=d["moves"]
    assert d["game"]["white_rating"]==1700 and d["game"]["black_rating"]==1800
    assert [x["elapsed_seconds"] for x in m]==[7,None,10,8]
    assert [x["elapsed_source"] for x in m]==["pgn_emt",None,"clock_delta","clock_delta"]
    assert m[2]["clocks_after"]=={"white":293,"black":302}
    assert d["timing"]["initial_clocks"]=={"white":300,"black":300}
    assert [(x["move_number"],x["color"]) for x in m]==[(1,"white"),(1,"black"),(2,"white"),(2,"black")]

def test_missing_clocks_and_black_start(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    raw='[SetUp "1"]\n[FEN "4k3/8/8/8/8/8/4P3/4K3 b - - 0 17"]\n[TimeControl "300+5"]\n\n17... Kf7 18. e4 *'
    g,p=next(parse(raw));store(g,p)
    d=detail(1)
    assert d["timing"]["initial_clocks"]=={"white":None,"black":None}
    assert [(x["move_number"],x["color"]) for x in d["moves"]]==[(17,"black"),(18,"white")]
    assert all(x["elapsed_seconds"] is None for x in d["moves"])

def test_daily_and_missing_clock_no_inference(tmp_path,monkeypatch):
    monkeypatch.setenv("CHESSBUILDER_DATA_DIR",str(tmp_path));migrate()
    raw='[TimeControl "300+5"]\n\n1. e4 {[%clk 0:05:00]} e5 2. Nf3 {[%clk 0:04:50]} Nc6 3. Bb5 Bc5 4. O-O {[%clk 0:04:30]} *'
    g,p=next(parse(raw));store(g,p,"chess.com","daily",meta={"speed":"daily"})
    assert all(x["elapsed_seconds"] is None for x in detail(1)["moves"])
    store(g,p,"chess.com","rapid",meta={"speed":"rapid"})
    m=detail(2)["moves"]
    assert m[2]["elapsed_seconds"]==15 and m[6]["elapsed_seconds"] is None
