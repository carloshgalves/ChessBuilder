"""Replay metadata from recorded PGN clocks and elapsed-time annotations."""
import io
import math
import re
import chess
import chess.pgn

def seconds(value):
    return value if isinstance(value,(int,float)) and math.isfinite(value) and value>=0 else None

def simple_control(control,speed):
    # Only single-stage Fischer/zero-increment controls permit subtraction.
    if speed in ("daily","correspondence"):return None
    m=re.fullmatch(r"([0-9]+)(?:\+([0-9]+))?",control or "")
    if not m or int(m[1])<=0:return None
    return int(m[1]),int(m[2] or 0)

def annotate_timing(game,moves):
    board=chess.Board(game["initial_fen"])
    control=simple_control(game["time_control"],game["speed"])
    initial=control[0] if control and board.fen()==chess.STARTING_FEN else None
    # Initial clock is nominal; first elapsed time is not inferred because
    # platforms differ in the treatment of the first move and increment.
    clocks={"white":initial,"black":initial}
    previous={"white":None,"black":None}
    try:
        pgn=chess.pgn.read_game(io.StringIO(game["pgn"]))
        nodes=list(pgn.mainline()) if pgn and not pgn.errors and pgn.board().fen()==board.fen() else []
        if [n.move.uci() for n in nodes]!=[m["uci"] for m in moves]:nodes=[]
    except (ValueError,TypeError):nodes=[]
    for index,move in enumerate(moves):
        color="white" if board.turn else "black"
        current=seconds(move["clock"])
        explicit=seconds(nodes[index].emt()) if nodes else None
        if explicit is not None:
            elapsed,source=explicit,"pgn_emt"
        elif control and current is not None and previous[color] is not None:
            difference=previous[color]+control[1]-current
            elapsed,source=(round(difference,3),"clock_delta") if difference>=0 else (None,None)
        else:elapsed,source=None,None
        before=previous[color]
        # Only a preceding recorded clock is evidence of time before a move.
        move["clock_before"]=before
        previous[color]=current
        clocks[color]=current
        move.update({"color":color,"move_number":board.fullmove_number,"elapsed_seconds":elapsed,
                     "elapsed_source":source,"clocks_after":dict(clocks)})
        board.push_uci(move["uci"])
    return {"initial_clocks":{"white":initial,"black":initial},
            "clocked_plies":sum(seconds(m["clock"]) is not None for m in moves),
            "timed_plies":sum(m["elapsed_seconds"] is not None for m in moves)}
