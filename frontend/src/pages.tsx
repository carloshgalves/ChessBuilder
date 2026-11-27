import Board from './Board'
export function Dashboard(){return <section><h1>Visão geral</h1><p>Seu espaço de análise e treinamento.</p><div className="panel"><Board/></div></section>}
export function Library(){return <section><h1>Partidas</h1><p>Nenhuma partida importada.</p></section>}
export function Import(){return <section><h1>Importar partidas</h1><p>Importação em desenvolvimento.</p></section>}
export function Analysis(){return <section><h1>Análise</h1><p>Fila de análise em desenvolvimento.</p></section>}
