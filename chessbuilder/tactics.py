"""Deterministic, conservative tactical evidence from legal principal variations."""
from collections import Counter
import chess

VALUES={chess.PAWN:1,chess.KNIGHT:3,chess.BISHOP:3,chess.ROOK:5,chess.QUEEN:9,chess.KING:0}
LABELS={
 "fork":"Garfo","pin":"Cravada","skewer":"Espeto","discovered_attack":"Ataque descoberto",
 "deflection":"Desvio","attraction":"Atração","interference":"Interferência",
 "overload":"Sobrecarga","remove_defender":"Remoção de defensor","zwischenzug":"Lance intermediário",
 "sacrifice":"Sacrifício","material":"Ganho de material","mate":"Mate"}
VERSION="tactical-v1"
def value(piece): return VALUES[piece.piece_type] if piece else 0

def balance(board,color):
    return sum(value(p)*(1 if p.color==color else -1) for p in board.piece_map().values())

def legal_line(board,pv):
    b=board.copy()
    steps=[]
    for token in pv or []:
        try: move=chess.Move.from_uci(token)
        except (ValueError,TypeError): return []
        if move not in b.legal_moves: return []
        before=b.copy()
        san=b.san(move)
        victim=b.piece_at(move.to_square)
        if b.is_en_passant(move): victim=chess.Piece(chess.PAWN,not b.turn)
        b.push(move)
        steps.append((before,move,b.copy(),san,victim))
    return steps

def ray_piece(board,square,dx,dy):
    x,y=chess.square_file(square)+dx,chess.square_rank(square)+dy
    while 0<=x<8 and 0<=y<8:
        s=chess.square(x,y)
        if board.piece_at(s): return s
        x+=dx;y+=dy
    return None

def rays(piece):
    orth=[(1,0),(-1,0),(0,1),(0,-1)]
    diag=[(1,1),(1,-1),(-1,1),(-1,-1)]
    return orth if piece.piece_type==chess.ROOK else diag if piece.piece_type==chess.BISHOP else orth+diag if piece.piece_type==chess.QUEEN else []

def threats(board,square,color):
    piece=board.piece_at(square)
    if not piece or piece.color!=color:return set()
    return {s for s in board.attacks(square) if (p:=board.piece_at(s)) and p.color!=color and (p.piece_type==chess.KING or value(p)>=2)}

def phase(board):
    nonp=sum(value(p) for p in board.piece_map().values() if p.piece_type not in (chess.PAWN,chess.KING))
    if nonp<=16:return "endgame"
    return "opening" if board.fullmove_number<=12 and nonp>=44 else "middlegame"

def scan(board,pv,color):
    """Return witnessed outcomes and structural candidates separately.
    A candidate is never asserted as a demonstrated cause.
    """
    steps=legal_line(board,pv)
    if not steps:return {"valid":False,"confirmed":{},"candidates":{}}
    confirmed={};candidates={}
    def put(target,theme,i,squares=(),detail=""):
        if theme not in target:
            target[theme]={"at":i+1,"san":steps[i][3],"squares":[chess.square_name(s) for s in squares],"detail":detail}
    start=balance(board,color)
    for i,(before,m,after,san,victim) in enumerate(steps):
        if before.turn!=color: continue
        piece=after.piece_at(m.to_square)
        if after.is_checkmate():
            put(confirmed,"mate",i,[m.to_square],"Xeque-mate executado na variante.")
        targets=threats(after,m.to_square,color)
        previous=threats(before,m.from_square,color)
        if len(targets)>=2 and targets!=previous:
            put(candidates,"fork",i,[m.to_square,*sorted(targets)],"Duas peças relevantes atacadas pelo mesmo atacante.")
            # Only elevate if the attacker actually captures a fork target after the reply,
            # with material benefit at the end of the supplied line.
            if i+2<len(steps):
                b2,m2,a2,_,v2=steps[i+2]
                if m2.from_square==m.to_square and m2.to_square in targets and v2 and balance(steps[-1][2],color)>start:
                    put(confirmed,"fork",i,[m.to_square,*sorted(targets)],"Um dos alvos é capturado pelo atacante após a resposta.")
        # New relative/absolute alignment along a slider ray.
        for s,p in after.piece_map().items():
            if p.color!=color:continue
            for dx,dy in rays(p):
                first=ray_piece(after,s,dx,dy)
                if first is None:continue
                front=after.piece_at(first)
                if front.color==color:continue
                second=ray_piece(after,first,dx,dy)
                if second is None:continue
                back=after.piece_at(second)
                if back.color==color:continue
                if before.piece_at(s)==p and ray_piece(before,s,dx,dy)==first and ray_piece(before,first,dx,dy)==second:continue
                if back.piece_type==chess.KING or value(back)>value(front):
                    put(candidates,"pin",i,[s,first,second],"Peça interposta entre atacante e alvo de maior valor.")
                if front.piece_type==chess.KING or value(front)>value(back):
                    put(candidates,"skewer",i,[s,first,second],"Alvo mais valioso à frente de outro alvo.")
                    if i+2<len(steps):
                        _,m2,_,_,v2=steps[i+2]
                        if m2.from_square==s and m2.to_square==second and v2 and balance(steps[-1][2],color)>start:
                            put(confirmed,"skewer",i,[s,first,second],"Após afastar o primeiro alvo, a variante captura o segundo.")
        for s,p in after.piece_map().items():
            if p.color==color and s!=m.to_square and before.piece_at(s)==p and rays(p):
                new=threats(after,s,color)-threats(before,s,color)
                for target in new:
                    if m.from_square not in chess.between(s,target):continue
                    put(candidates,"discovered_attack",i,[s,m.from_square,target],"O lance libera a linha de outra peça.")
                    if i+2<len(steps):
                        _,m2,_,_,v2=steps[i+2]
                        if m2.from_square==s and m2.to_square==target and v2 and balance(steps[-1][2],color)>start:
                            put(confirmed,"discovered_attack",i,[s,target],"A peça liberada captura o alvo na continuação.")
        if victim:
            defended=[s for s in before.attacks(m.to_square) if (p:=before.piece_at(s)) and p.color!=color]
            if defended:
                put(candidates,"remove_defender",i,[m.to_square,*defended],"Peça capturada defendia outros alvos.")
            if len(defended)>=2:
                put(candidates,"overload",i,[m.to_square,*defended],"Defensor tinha múltiplas responsabilidades potenciais.")
        if i+1<len(steps):
            _,reply,reply_after,_,reply_victim=steps[i+1]
            if reply_victim and reply.to_square==m.to_square and value(reply_victim)>value(victim):
                if balance(reply_after,color)<balance(before,color):
                    put(candidates,"sacrifice",i,[m.to_square],"Entrega temporária de material na variante.")
                    if balance(steps[-1][2],color)>start or (steps[-1][2].is_checkmate() and steps[-1][2].turn!=color):
                        put(confirmed,"sacrifice",i,[m.to_square],"Entrega seguida de recuperação material ou mate na linha.")
            if after.is_check() and reply.from_square!=reply.to_square:
                # Forced response alone is only a potential attraction/deflection.
                if reply_victim:
                    put(candidates,"attraction",i,[reply.to_square],"A resposta captura na casa indicada.")
                guarded=[s for s in after.attacks(reply.from_square) if (p:=after.piece_at(s)) and p.color!=color]
                if guarded:
                    put(candidates,"deflection",i,[reply.from_square,*guarded],"A resposta desloca um defensor.")
        if i>=2 and after.is_check() and victim is None:
            put(candidates,"zwischenzug",i,[m.to_square],"Xeque intermediário; a ordem dos lances exige confirmação.")
        for s,p in before.piece_map().items():
            if p.color==color or not rays(p) or after.piece_at(s)!=p:continue
            for target in before.attacks(s):
                q=before.piece_at(target)
                if q and q.color==p.color and m.to_square in chess.between(s,target) and target not in after.attacks(s):
                    put(candidates,"interference",i,[s,m.to_square,target],"Interrupção de uma linha defensiva.")
    end=steps[-1][2]
    gain=balance(end,color)-start
    if gain>=2 and len(steps)>=2:
        # A finite PV is evidence of material change, not proof of a forced tactic.
        captures=[i for i,s in enumerate(steps) if s[0].turn==color and s[4]]
        if captures:put(confirmed,"material",captures[-1],[],"Saldo material na linha: +"+str(gain)+".")
    return {"valid":True,"confirmed":confirmed,"candidates":candidates}

def diagnose(board,played,payload):
    from chessbuilder.review import metrics
    m=metrics(payload)
    if m is None or played not in board.legal_moves:return {"version":VERSION,"phase":phase(board),"status":"abstain","findings":[],"loss":None}
    color=board.turn
    best=payload["best"].get("pv",[])
    actual=payload["played"].get("pv",[])
    expected="white" if color else "black"
    if payload.get("perspective")!=expected or not actual or actual[0]!=played.uci() or not best:
        return {"version":VERSION,"phase":phase(board),"status":"abstain","findings":[],"loss":m["win_loss"]}
    good=scan(board,best,color);bad=scan(board,actual,not color)
    actual_own=scan(board,actual,color);best_opp=scan(board,best,not color)
    if not all(x["valid"] for x in (good,bad,actual_own,best_opp)):
        return {"version":VERSION,"phase":phase(board),"status":"abstain","findings":[],"loss":m["win_loss"]}
    loss=m["win_loss"];findings=[]
    def add(theme,evidence,kind,side,confirmed):
        label=LABELS[theme]
        findings.append({"theme":theme,"label":label,"kind":kind,"side":side,"confidence":"line_witness" if confirmed else "candidate","severity_pp":round(loss,2),"evidence":evidence,"explanation":label+": "+evidence["detail"]+(" Evidência na variante analisada." if confirmed else " Hipótese estrutural, sem causalidade demonstrada.")})
    if loss>=5:
        for theme,ev in good["confirmed"].items():
            if theme not in actual_own["confirmed"]:add(theme,ev,"missed","best",True)
        for theme,ev in bad["confirmed"].items():
            if theme not in best_opp["confirmed"]:add(theme,ev,"allowed","played",True)
    elif loss<=2:
        for theme,ev in actual_own["confirmed"].items():add(theme,ev,"exploited","played",True)
    for side,data in (("best",good),("played",bad if loss>=5 else actual_own)):
        for theme,ev in data["candidates"].items():
            if not any(f["theme"]==theme and f["side"]==side for f in findings):
                add(theme,ev,"candidate",side,False)
    return {"version":VERSION,"phase":phase(board),"status":"witnessed" if any(f["confidence"]=="line_witness" for f in findings) else "abstain","findings":findings,"loss":round(loss,2)}
