import Board from './Board'
export function Dashboard(){return <section><h1>Visão geral</h1><p>Seu espaço de análise e treinamento.</p><div className="panel"><Board/></div></section>}
export {default as Library} from './Library'
export {default as Import} from './Import'
export function Analysis(){return <section><h1>Análise</h1><p>Fila de análise em desenvolvimento.</p></section>}
