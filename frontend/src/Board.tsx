const glyph:Record<string,string>={K:'♔',Q:'♕',R:'♖',B:'♗',N:'♘',P:'♙',k:'♚',q:'♛',r:'♜',b:'♝',n:'♞',p:'♟'};
export const START='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
export function squares(fen:string, flipped=false):string[]{
 const arr=fen.split(' ')[0].split('/').flatMap(rank=>[...rank].flatMap(c=>/[1-8]/.test(c)?Array(Number(c)).fill(''):[c]));
 if(arr.length!==64) throw new Error('FEN inválida');
 return flipped?arr.reverse():arr;
}
export default function Board({fen=START,flipped=false,last=[],onSquare,selected,targets=[]}:{fen?:string,flipped?:boolean,last?:string[],onSquare?:(square:string)=>void,selected?:string,targets?:string[]}){
 return <div className="board" role={onSquare?'group':'img'} aria-label={'Tabuleiro '+fen}>{squares(fen,flipped).map((p,i)=>{const n=flipped?63-i:i;const sq='abcdefgh'[n%8]+(8-Math.floor(n/8));const cls='square '+((Math.floor(i/8)+i%8)%2?'dark':'light')+(last.includes(sq)?' marked':'')+(selected===sq?' selected':'')+(targets.includes(sq)?' target':'');const content=<>{glyph[p]||''}{i%8===0&&<small>{sq[1]}</small>}{i>=56&&<em>{sq[0]}</em>}</>;return onSquare?<button type="button" key={i} className={cls} title={sq} aria-label={sq+(p?' '+glyph[p]:'')} aria-pressed={selected===sq} onClick={()=>onSquare(sq)}>{content}</button>:<div key={i} className={cls} title={sq}>{content}</div>})}</div>
}
