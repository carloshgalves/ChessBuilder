import {useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Dashboard,Library,Import,Analysis} from './pages'
import './style.css'
const pages={dashboard:Dashboard,library:Library,import:Import,analysis:Analysis}
const labels={dashboard:'Visão geral',library:'Partidas',import:'Importar',analysis:'Análise'}
type Page=keyof typeof pages;
function App(){const [page,setPage]=useState<Page>(()=>new URLSearchParams(location.search).has('game')?'library':'dashboard');const Current=pages[page];return <div className="shell"><aside><div className="brand">♞ Chess Builder</div><nav aria-label="Navegação principal">{(Object.keys(pages) as Page[]).map(p=><button key={p} aria-current={p===page?'page':undefined} onClick={()=>setPage(p)}>{labels[p]}</button>)}</nav><footer>Estudo • Análise • Evolução</footer></aside><main><Current/></main></div>}
createRoot(document.getElementById('root')!).render(<App/>)
