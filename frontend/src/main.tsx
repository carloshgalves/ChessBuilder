import {useEffect,useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Dashboard,Library,Import,Analysis,Training,Coach,Endgames,Repertoire,Woodpecker,Historical,Books,Bots,Tournaments} from './pages'
import './style.css'
import './workspace.css'
import {Appearance} from './Board'
const pages={dashboard:Dashboard,library:Library,import:Import,analysis:Analysis,training:Training,coach:Coach,endgames:Endgames,repertoire:Repertoire,woodpecker:Woodpecker,historical:Historical,books:Books,bots:Bots,tournaments:Tournaments}
const labels={dashboard:'Visão geral',library:'Partidas',import:'Importar',analysis:'Análise',training:'Treino',coach:'Treinador',endgames:'Finais',repertoire:'Aberturas',woodpecker:'Woodpecker',historical:'História',books:'Livros',bots:'Adversários',tournaments:'Torneios'}
type Page=keyof typeof pages;
const groups:{label:string,pages:Page[]}[]=[{label:'Meu xadrez',pages:['dashboard','library','import','analysis']},{label:'Treinar',pages:['training','coach','endgames','repertoire','woodpecker']},{label:'Estudar',pages:['historical','books']},{label:'Jogar',pages:['bots','tournaments']}]
const icons:Record<Page,string>={dashboard:'▤',library:'♟',import:'↓',analysis:'⌕',training:'♞',coach:'◎',endgames:'♔',repertoire:'♜',woodpecker:'↻',historical:'♛',books:'▥',bots:'♚',tournaments:'⚑'}
type Theme='graphite'|'forest'|'slate'
const themeLabels:Record<Theme,string>={graphite:'Grafite',forest:'Verde',slate:'Ardósia'}
function pref(key:string){try{return localStorage.getItem(key)}catch{return null}}
function save(key:string,value:string){try{localStorage.setItem(key,value)}catch{}}
function initialPage():Page{const q=new URLSearchParams(location.search);if(q.has('game'))return 'library';if(q.has('exercise'))return 'training';if(q.has('coach'))return 'coach';if(q.has('cycle'))return 'woodpecker';if(q.has('bot'))return 'bots';const p=q.get('page');return p&&Object.prototype.hasOwnProperty.call(pages,p)?p as Page:'dashboard'}
function App(){
 const [page,setPage]=useState<Page>(initialPage),[routeVersion,setRouteVersion]=useState(0),[compact,setCompact]=useState(()=>pref('cb.nav.compact')==='1'),[mobileOpen,setMobileOpen]=useState(false),[theme,setTheme]=useState<Theme>(()=>{const p=pref('cb.workspace.theme');return p&&Object.prototype.hasOwnProperty.call(themeLabels,p)?p as Theme:'graphite'})
 useEffect(()=>{const handler=()=>{setPage(initialPage());setRouteVersion(x=>x+1);setMobileOpen(false)};window.addEventListener('popstate',handler);return()=>window.removeEventListener('popstate',handler)},[])
 const Current=pages[page]
 function navigate(p:Page){history.pushState(null,'',location.pathname+'?page='+p);setPage(p);setRouteVersion(x=>x+1);setMobileOpen(false);window.scrollTo(0,0)}
 return <div className={'shell workspace '+(compact?'nav-compact':'')} data-workspace-theme={theme}><aside className={mobileOpen?'nav-open':''}><div className="workspace-brand"><button className="brand" aria-label="Chess Builder · início" onClick={()=>navigate('dashboard')}><span className="brand-icon">♞</span><span className="brand-wordmark">Chess Builder<small>Estudo e desempenho</small></span></button><button className="nav-toggle" aria-label={compact?'Expandir navegação':'Recolher navegação'} onClick={()=>setCompact(v=>{save('cb.nav.compact',v?'0':'1');return !v})}>{compact?'»':'«'}</button></div><nav aria-label="Navegação principal">{groups.map(g=><div className="nav-group" key={g.label}><div className="nav-group-title">{g.label}</div>{g.pages.map(p=><button key={p} aria-label={labels[p]} title={labels[p]} aria-current={p===page?'page':undefined} onClick={()=>navigate(p)}><span className="nav-icon" aria-hidden="true">{icons[p]}</span><span className="nav-label">{labels[p]}</span></button>)}</div>)}</nav><footer>Biblioteca local · Stockfish</footer></aside><main><header className="app-topbar"><button className="mobile-menu" aria-expanded={mobileOpen} onClick={()=>setMobileOpen(v=>!v)}>☰ Menu</button><div className="workspace-breadcrumb"><span>Chess Builder</span><span aria-hidden="true">/</span><strong>{labels[page]}</strong></div><div className="workspace-settings"><label className="workspace-theme"><span>Interface</span><select aria-label="Tema da interface" value={theme} onChange={e=>{const v=e.target.value as Theme;setTheme(v);save('cb.workspace.theme',v)}}>{Object.entries(themeLabels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label><Appearance/></div></header><Current key={routeVersion}/></main></div>
}
createRoot(document.getElementById('root')!).render(<App/>)
