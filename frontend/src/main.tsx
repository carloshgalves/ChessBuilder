import {useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Dashboard,Library,Import,Analysis,Training,Coach,Endgames,Repertoire,Woodpecker} from './pages'
import './style.css'
import {Appearance} from './Board'
const pages={dashboard:Dashboard,library:Library,import:Import,analysis:Analysis,training:Training,coach:Coach,endgames:Endgames,repertoire:Repertoire,woodpecker:Woodpecker}
const labels={dashboard:'Visão geral',library:'Partidas',import:'Importar',analysis:'Análise',training:'Treino',coach:'Treinador',endgames:'Finais',repertoire:'Aberturas',woodpecker:'Woodpecker'}
type Page=keyof typeof pages;
function App(){const [page,setPage]=useState<Page>(()=>new URLSearchParams(location.search).has('game')?'library':new URLSearchParams(location.search).has('exercise')?'training':new URLSearchParams(location.search).has('coach')?'coach':new URLSearchParams(location.search).has('cycle')?'woodpecker':'dashboard');const [routeVersion,setRouteVersion]=useState(0);const Current=pages[page];return <div className="shell"><aside><div className="brand">♞ Chess Builder</div><nav aria-label="Navegação principal">{(Object.keys(pages) as Page[]).map(p=><button key={p} aria-current={p===page?'page':undefined} onClick={()=>{history.replaceState(null,"",location.pathname);setPage(p);setRouteVersion(x=>x+1)}}>{labels[p]}</button>)}</nav><Appearance/><footer>Estudo • Análise • Evolução</footer></aside><main><Current key={routeVersion}/></main></div>}
createRoot(document.getElementById('root')!).render(<App/>)
