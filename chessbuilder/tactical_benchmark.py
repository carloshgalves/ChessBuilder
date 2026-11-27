"""Small synthetic detector regression corpus; not an independent strength benchmark."""
import json
import chess
from chessbuilder.tactics import LABELS,scan

# Each fixture annotates only the themes explicitly named. Unannotated
# themes are excluded from denominators instead of being treated as negatives.
CORPUS=[
 ("mate","7k/5Q2/6K1/8/8/8/8/8 w - - 0 1",["f7g7"],{"mate":(False,True)}),
 ("fork-realized","3q4/4k3/8/4N3/8/8/8/4K3 w - - 0 1",["e5c6","e7f8","c6d8"],{"fork":(True,True),"material":(False,True)}),
 ("fork-unrealized","3q4/4k3/8/4N3/8/8/8/4K3 w - - 0 1",["e5c6"],{"fork":(True,False)}),
 ("pin-alignment","4k3/8/4n3/8/8/8/8/R5K1 w - - 0 1",["a1e1"],{"pin":(True,False)}),
 ("skewer","8/4q3/8/4k3/8/8/8/R5K1 w - - 0 1",["a1e1","e5f5","e1e7"],{"skewer":(True,True)}),
 ("discovery","q3k3/8/8/8/8/B7/8/R3K3 w - - 0 1",["a3b4","e8d7","a1a8"],{"discovered_attack":(True,True)}),
 ("multiple-guards","7k/3n4/8/3r1b2/8/8/Q7/6K1 w - - 0 1",["a2d5"],{"remove_defender":(True,False),"overload":(True,False)}),
 ("sacrifice-unrealized","4k3/4p3/8/8/8/8/8/4R1K1 w - - 0 1",["e1e7","e8e7"],{"sacrifice":(True,False),"attraction":(True,False)}),
 ("deflection","3bk3/8/8/8/8/8/8/R5K1 w - - 0 1",["a1e1","e8f7"],{"deflection":(True,False)}),
 ("interference","r6r/8/2N1k3/8/8/8/8/6K1 w - - 0 1",["c6b8"],{"interference":(True,False)}),
 ("intermediate-check","3qk3/8/8/3p4/4PN2/8/8/R5K1 w - - 0 1",["e4d5","d8d5","a1e1"],{"zwischenzug":(True,False)}),
 ("quiet",chess.STARTING_FEN,["e2e4","e7e5","g1f3"],{theme:(False,False) for theme in LABELS}),
]

def evaluate():
    by_theme={theme:{kind:{"tp":0,"fp":0,"fn":0,"tn":0} for kind in ("candidate","witness")} for theme in LABELS}
    invalid=[]
    for name,fen,pv,labels in CORPUS:
        board=chess.Board(fen)
        result=scan(board,pv,board.turn)
        if not board.is_valid() or not result["valid"]:
            invalid.append(name)
            continue
        for theme,(candidate,witness) in labels.items():
            for kind,expected,found in (("candidate",candidate,theme in result["candidates"]),("witness",witness,theme in result["confirmed"])):
                by_theme[theme][kind]["tp" if expected and found else "fp" if found else "fn" if expected else "tn"]+=1
    def ratio(n,d):return n/d if d else None
    for theme in by_theme.values():
        for counts in theme.values():
            tp,fp,fn,tn=(counts[k] for k in ("tp","fp","fn","tn"))
            counts.update({"precision":ratio(tp,tp+fp),"recall":ratio(tp,tp+fn),"coverage":tp+fp+fn+tn,"positive_support":tp+fn})
    return {"corpus":"synthetic-regression-v1","fixtures":len(CORPUS),"invalid":invalid,"themes":by_theme}

if __name__=="__main__":
    print(json.dumps(evaluate(),indent=2))
