"""Conservative positional feature contrasts from matched legal continuations."""
import chess
from chessbuilder.tactics import legal_line,scan,balance,phase
VERSION="strategy-v1"
LABELS={
 "pawn_structure":"Estrutura de peões","weak_squares":"Casas centrais vulneráveis",
 "open_files":"Colunas de torre","activity":"Atividade das peças",
 "mobility":"Mobilidade geométrica","king_safety":"Segurança do rei",
 "bishop_pair":"Par de bispos","space":"Espaço controlado",
 "passed_pawns":"Peões passados","exchanges":"Trocas",
 "prophylaxis":"Restrição da atividade adversária","compensation":"Compensação material"}
THRESHOLDS={"pawn_structure":1,"weak_squares":2,"open_files":1,"activity":3,"mobility":5,"king_safety":2,"bishop_pair":1,"space":3,"passed_pawns":1,"prophylaxis":5}

def side_features(b,color):
    pawns=b.pieces(chess.PAWN,color);enemy=b.pieces(chess.PAWN,not color)
    own=b.occupied_co[color];files={f:[s for s in pawns if chess.square_file(s)==f] for f in range(8)}
    isolated=sum(len(s) for f,s in files.items() if not any(files.get(x) for x in (f-1,f+1)))
    doubled=sum(max(0,len(s)-1) for s in files.values())
    islands=sum(bool(files[f]) and (f==0 or not files[f-1]) for f in range(8))
    passed=0
    for s in pawns:
        f,r=chess.square_file(s),chess.square_rank(s)
        if not any(abs(chess.square_file(t)-f)<=1 and ((chess.square_rank(t)>r) if color else (chess.square_rank(t)<r)) for t in enemy):passed+=1
    open_files=0
    for s in b.pieces(chess.ROOK,color):
        f=chess.square_file(s)
        if not files[f]:open_files+=2 if not any(chess.square_file(t)==f for t in enemy) else 1
    controlled=set();activity=mobility=0
    for s,p in b.piece_map().items():
        if p.color!=color:continue
        targets=set(b.attacks(s))
        if b.is_pinned(color,s):targets={t for t in targets if t in b.pin(color,s)}
        controlled.update(targets)
        if p.piece_type in (chess.KNIGHT,chess.BISHOP,chess.ROOK,chess.QUEEN):
            useful={t for t in targets if not (chess.BB_SQUARES[t]&own)}
            mobility+=len(useful)
            activity+=sum(1 for t in useful if (chess.square_rank(t)>=4 if color else chess.square_rank(t)<=3))
    space=sum(1 for s in controlled if (chess.square_rank(s)>=4 if color else chess.square_rank(s)<=3))
    central=[chess.square(f,r) for f in range(2,6) for r in ([2,3] if color else [4,5])]
    weak=sum(1 for s in central if not (b.attackers(color,s)&pawns) and b.is_attacked_by(not color,s))
    king=b.king(color);shelter=pressure=0
    if king is not None:
        ring=set(chess.SquareSet(chess.BB_KING_ATTACKS[king]))|{king}
        shelter=sum(1 for s in ring if s in pawns)
        pressure=sum(1 for s in ring if b.is_attacked_by(not color,s))
    bishops=b.pieces(chess.BISHOP,color)
    pair=int(bool(bishops&chess.SquareSet(chess.BB_LIGHT_SQUARES)) and bool(bishops&chess.SquareSet(chess.BB_DARK_SQUARES)))
    return {"pawn_structure":-isolated-doubled-islands,"weak_squares":-weak,"open_files":open_files,"activity":activity,"mobility":mobility,"king_safety":shelter-pressure,"bishop_pair":pair,"space":space,"passed_pawns":passed}

def features(b,color):
    own=side_features(b,color);opp=side_features(b,not color)
    relative={k:own[k]-opp[k] for k in own}
    relative["prophylaxis"]=-opp["mobility"]
    relative["exchanges"]=sum(1 for p in b.piece_map().values() if p.color==color and p.piece_type not in (chess.KING,chess.PAWN))
    relative["compensation"]=balance(b,color)
    return relative

def compare_lines(board,good,bad):
    """Compare the same number of plies; no extrapolation from short PVs."""
    a=legal_line(board,good);z=legal_line(board,bad)
    if len(a)<3 or len(z)<3:return []
    color=board.turn;sample=[i for i in (0,2,4) if i<min(len(a),len(z))]
    if len(sample)<2:return []
    before=features(board,color)
    best=[features(a[i][2],color) for i in sample];played=[features(z[i][2],color) for i in sample]
    findings=[]
    for theme,threshold in THRESHOLDS.items():
        differences=[x[theme]-y[theme] for x,y in zip(best,played)]
        if min(differences)<threshold:continue
        # Prophylaxis is a measured restriction, not an inferred intention.
        findings.append({"theme":"strategy:"+theme,"label":LABELS[theme],"kind":"missed","confidence":"persistent_contrast","side":"best","evidence":{"before":before[theme],"best":[x[theme] for x in best],"played":[x[theme] for x in played],"plies":[i+1 for i in sample],"best_pv":good[:sample[-1]+1],"played_pv":bad[:sample[-1]+1]},"explanation":f"{LABELS[theme]}: o indicador relativo favorece a linha de referência nos {len(sample)} pontos comparados. A relação causal não está demonstrada."})
    # Exchanges/compensation are descriptive evidence, not scored opportunities.
    return findings

def diagnose_strategy(board,move,payload):
    from chessbuilder.review import metrics,win_percent
    out={"version":VERSION,"status":"abstain","findings":[],"type":"unclassified","reason":"insufficient_evidence"}
    m=metrics(payload);best=payload.get("best",{});actual=payload.get("played",{})
    if not m or payload.get("perspective")!=("white" if board.turn else "black") or actual.get("pv",[])[:1]!=[move.uci()]:return out
    if payload.get("settings",{}).get("level") not in ("deep","detailed") or min(best.get("depth") or 0,actual.get("depth") or 0)<12:return out
    if best.get("mate") is not None or actual.get("mate") is not None:out["reason"]="mate";return out
    good=best.get("pv",[]);bad=actual.get("pv",[])
    kind="missed";loss=m["win_loss"]
    if loss<=2:
        # An avoided deterioration supplies a witnessed success denominator.
        alt=next((x for x in payload.get("alternatives",[]) if x.get("pv",[])[:1]!=[move.uci()] and x.get("mate") is None and (x.get("depth") or 0)>=12 and win_percent(x) is not None and win_percent(actual)-win_percent(x)>=5),None)
        if not alt:return out
        good,bad=actual.get("pv",[]),alt["pv"];kind="exploited";loss=win_percent(actual)-win_percent(alt)
    elif loss<5 or good[:1]==bad[:1]:return out
    a=legal_line(board,good);z=legal_line(board,bad)
    if len(a)<3 or len(z)<3:return out
    # Tactical outcomes dominate positional attribution. Avoid labelling a
    # missing piece, mate or forced combination a purely strategic error.
    own=board.turn
    if any(scan(board,pv,c)["confirmed"] for pv in (good,bad) for c in (own,not own)):
        out["reason"]="tactical_witness";return out
    n=min(5,len(a),len(z))
    if any(abs(balance(a[i][2],own)-balance(z[i][2],own))>1 for i in range(n)):
        out["reason"]="material_disparity";return out
    findings=compare_lines(board,good,bad)
    for f in findings:f["kind"]=kind;f["severity_pp"]=round(loss,2)
    out.update({"status":"contrast" if findings else "abstain","findings":findings,"type":("technical_candidate" if phase(board)=="endgame" else "strategic_candidate") if findings else "unclassified","reason":"persistent_feature" if findings else "no_persistent_feature","tradeoff":{"material":[balance(a[n-1][2],own),balance(z[n-1][2],own)],"pieces":[len(a[n-1][2].piece_map()),len(z[n-1][2].piece_map())]}})
    return out
